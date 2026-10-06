"""Auto-continue: re-prompt a finished chat so the agent keeps iterating.

A composer toggle (``params.auto_continue``) asks the server to append a static
follow-up user prompt each time a turn of that chat ends *cleanly*, so the agent
starts another round instead of waiting for a human.

It is built on the existing queued-input machinery on purpose: the follow-up is
written as a ``meta.queued`` user message, so ``process_pending_chat_inputs``
picks it up from the finished task's own ``finally`` block and the queued-message
UI already renders it -- the user can watch it, edit it, send it now, or delete
it. No second scheduler, no second queue.

A loop that re-prompts itself spends money and never stops on its own, so every
exit is guarded:

* only a *success* queues -- not a cancel, an error, max-iterations, or a turn
  that stopped for tool approval or ``ask_user`` (those ``return`` before the
  success path and are excluded for free);
* a run counter in ``meta.auto_continue_state`` caps the chain;
* the prompt ends with a fixed phrase the agent says when there is genuinely
  nothing left, which ends the chain before the cap;
* internal/subagent chats never auto-continue -- that would fork agents forever.

The counter is server-owned state kept *out* of ``meta.params``, because the
client rewrites ``params`` wholesale on every settings save and would erase it.
"""

from __future__ import annotations

import logging

from cptr.models import Chat, ChatMessage, is_internal_chat
from cptr.utils.config import now_ms

logger = logging.getLogger(__name__)

# ── Configuration (client-owned: lives in meta.params.auto_continue) ──

DEFAULT_PROMPT = (
    "Keep going. Re-read what you just produced and find what is still wrong, "
    "incomplete, or could be done better, then actually do that next round of "
    "work with your tools instead of summarising what you would do. Take the "
    "problems one at a time and keep going until there is nothing material "
    "left. If there is genuinely nothing left to improve, reply with just "
    "\"nothing left to iterate\" and stop."
)

MAX_PROMPT_CHARS = 4000
DEFAULT_MAX_RUNS = 25
MAX_RUNS_CEILING = 200

# The agent's own off-switch. Matched case-insensitively against the tail of its
# final text, so a chat that is genuinely finished ends before the run cap.
STOP_MARKERS = (
    "nothing left to iterate",
    "nothing further to iterate",
    "no further improvements",
)

CONFIG_KEY = "auto_continue"
STATE_KEY = "auto_continue_state"


# ── Reading config and state ───────────────────────────────────


def _config(chat: Chat | None) -> dict | None:
    """Return the enabled auto-continue config for a chat, or None."""
    if chat is None:
        return None
    params = (chat.meta or {}).get("params")
    if not isinstance(params, dict):
        return None
    config = params.get(CONFIG_KEY)
    if not isinstance(config, dict) or not config.get("enabled"):
        return None
    return config


def auto_continue_prompt(config: dict) -> str:
    """The follow-up text: the user's, else the static default."""
    prompt = config.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()[:MAX_PROMPT_CHARS]
    return DEFAULT_PROMPT


def _max_runs(config: dict) -> int:
    """Cap on consecutive auto-follow-ups, clamped to something survivable."""
    try:
        value = int(config.get("max_runs", DEFAULT_MAX_RUNS))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_RUNS
    return max(1, min(value, MAX_RUNS_CEILING))


def _state(chat: Chat | None) -> dict:
    """Return the server-owned run counter for a chat (never None)."""
    state = ((chat.meta or {}).get(STATE_KEY)) if chat else None
    if not isinstance(state, dict):
        state = {}
    try:
        runs = int(state.get("runs", 0))
    except (TypeError, ValueError):
        runs = 0
    return {"runs": max(0, runs)}


async def _write_state(chat_id: str, chat: Chat, runs: int) -> None:
    """Persist the run counter, merging onto the chat's current meta."""
    fresh = await Chat.get_by_id(chat_id)  # re-read: settings may have changed
    meta = dict((fresh.meta if fresh else chat.meta) or {})
    meta[STATE_KEY] = {"runs": max(0, int(runs))}
    await Chat.update_meta(chat_id, meta, now_ms())


async def reset_runs(chat_id: str, config_on: bool = True) -> None:
    """Re-arm the run counter.

    Called when the toggle is switched on, when its settings are edited, and
    when the user sends a real prompt: each is a deliberate "start a fresh
    chain" signal, and without this a chat that burned its budget could never
    auto-continue again without toggling it off and on.
    """
    if not config_on:
        return
    chat = await Chat.get_by_id(chat_id)
    if chat is None or _config(chat) is None:
        return
    await _write_state(chat_id, chat, 0)


# ── Stop detection ────────────────────────────────────────────


def assistant_plain_text(content: str, output_items: list | None) -> str:
    """Flatten an assistant turn into searchable text.

    Tool-heavy turns leave ``content`` empty and keep the prose in
    ``output`` items, so checking ``content`` alone would read every such turn
    as silent and keep looping.
    """
    parts = [content or ""]
    for item in output_items or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "output_text":
                parts.append(block.get("text") or "")
    return "\n".join(p for p in parts if p)


def said_stop(text: str) -> bool:
    """True when the agent closed with its own "nothing left" phrase.

    Matched against the tail so the phrase still counts when the agent
    summarises first, but a mid-answer aside does not stop the chain.
    """
    tail = (text or "")[-600:].lower()
    return any(marker in tail for marker in STOP_MARKERS)


# ── Truncation (B-023 / B-027) ────────────────────────────────

#: Stop reasons that mean the provider cut the answer short, i.e. the turn is
#: NOT a clean completion. Auto-continue must not iterate on one: doing so
#: burns tokens finishing a half-answer instead of surfacing the truncation.
TRUNCATION_FINISH_REASONS = frozenset(
    {
        "length",  # OpenAI / xAI / OpenRouter
        "max_tokens",  # Anthropic
        "max_output_tokens",  # Responses API
        "content_filter",  # OpenAI
        "refusal",  # Anthropic
        "incomplete",  # Responses API fallback
    }
)


def is_truncated(finish_reason: str | None) -> bool:
    """Whether a provider stop reason means the output was cut short.

    ``None`` is *not* truncation: several transports report no reason at all
    (the agent-target path never does), so treating "unknown" as truncation
    would silently disable auto-continue on those for every single turn.
    """
    if not finish_reason:
        return False
    return finish_reason.strip().lower() in TRUNCATION_FINISH_REASONS


# ── Queueing ──────────────────────────────────────────────────


async def maybe_queue_auto_continue(
    *,
    chat_id: str,
    assistant_message_id: str,
    model: str | None,
    content: str = "",
    output_items: list | None = None,
    finish_reason: str | None = None,
) -> str | None:
    """Append the follow-up prompt if this chat wants one. Returns a reason.

    Returns a short string when it queued (for the log) and None when it
    declined. Never raises: a failed follow-up must not disturb a turn that
    already succeeded.

    ``finish_reason`` is the provider's stop signal when the transport reports
    one; a truncated turn is declined rather than iterated on (B-027).
    """
    try:
        chat = await Chat.get_by_id(chat_id)
        config = _config(chat)
        if config is None:
            return None

        # An internal chat is a subagent or a harness run; re-prompting it
        # would spawn work nobody is watching, forever.
        if is_internal_chat(chat.meta):
            return None

        # B-023/B-027: a truncated turn is not a completion. Re-prompting here
        # would ask the agent to continue from a half-written answer, so the
        # chain would burn the entire run cap on it and never say why.
        if is_truncated(finish_reason):
            return f"turn was truncated ({finish_reason})"

        if said_stop(assistant_plain_text(content, output_items)):
            return "agent asked to stop"

        state = _state(chat)
        cap = _max_runs(config)
        if state["runs"] >= cap:
            return f"run cap reached ({cap})"

        # Don't stack follow-ups: one outstanding auto prompt is the whole
        # design, and a second would fork the chat into two racing branches.
        for message in await ChatMessage.get_all_by_chat(chat_id):
            if message.role == "user" and (message.meta or {}).get("auto"):
                return "a follow-up is already queued"

        await ChatMessage.create(
            chat_id=chat_id,
            role="user",
            content=auto_continue_prompt(config),
            parent_id=assistant_message_id,
            model=model,
            meta={"queued": True, "auto": True},
            created_at=now_ms(),
        )
        await _write_state(chat_id, chat, state["runs"] + 1)
        logger.info(
            "[auto-continue] chat %s: queued follow-up %d/%d",
            chat_id[:8],
            state["runs"] + 1,
            cap,
        )
        return f"queued run {state['runs'] + 1}/{cap}"

    except Exception:
        logger.warning("[auto-continue] chat %s: queueing failed", chat_id[:8], exc_info=True)
        return None
