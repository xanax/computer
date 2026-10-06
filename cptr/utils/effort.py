"""Reasoning-effort support, per connection and per model.

Every provider body in ``cptr/utils/ai.py`` ends with a plain
``body.update(request_params)``, so an effort value only has to reach
``request_params`` to reach the wire — there is no per-provider field to set
here. What this module answers is the harder half: *which* models take a
reasoning-effort knob at all, and under which key.

The three shapes in the wild:

* ``reasoning_effort`` — OpenAI (``low``/``medium``/``high``), xAI, OpenRouter.
* ``reasoning: {effort: …}`` — the Responses API and OpenRouter's Anthropic
  models.
* ``thinking: {type, budget_tokens}`` — Anthropic's own knob, expressed in
  *tokens* rather than named levels.

Values are derived from the model id and the connection, with two overrides:
``chat.models`` config (per model or ``"*"``) may declare ``effort_levels``
explicitly, and ``reasoning_effort_param`` may rename the wire key. That keeps
an unknown or brand-new model reachable without a code change — the admin
Models page already edits exactly that config.
"""

from __future__ import annotations

import re
from typing import Any

# ── Named levels ──────────────────────────────────────────────
#
# The lowest level is the provider's default: selecting it writes nothing to
# the request, so a model left alone behaves exactly as it did before this
# feature existed. "Default" is therefore the initial selection, and the
# dropdown starts on it rather than on a level the user did not ask for.

LEVEL_DEFAULT = "default"
LEVEL_MINIMAL = "minimal"
LEVEL_LOW = "low"
LEVEL_MEDIUM = "medium"
LEVEL_HIGH = "high"

# Ascending. Every provider we support accepts a prefix of this list, which is
# why "high" is safe to offer everywhere: the more levels a provider has, the
# more of this list it takes.
NAMED_LEVELS = (LEVEL_MINIMAL, LEVEL_LOW, LEVEL_MEDIUM, LEVEL_HIGH)

# Levels an OpenAI-style ``reasoning_effort`` accepts.
OPENAI_LEVELS = (LEVEL_MINIMAL, LEVEL_LOW, LEVEL_MEDIUM, LEVEL_HIGH)

# Wire keys by shape.
PARAM_OPENAI = "reasoning_effort"
PARAM_NESTED = "reasoning"
PARAM_ANTHROPIC = "thinking"

# Anthropic's budget, in output tokens, for each named level. Anthropic has no
# named levels at all — it takes a token budget — so these are the numbers that
# make "low"/"high" mean anything on an Anthropic model. Doubling from low to
# high is the usual shape; the caps stay well inside the 4096 ``max_tokens``
# that ``stream_anthropic`` sets, because reasoning tokens are drawn from that
# same allowance.
ANTHROPIC_BUDGETS = {
    LEVEL_MINIMAL: 1024,
    LEVEL_LOW: 2048,
    LEVEL_MEDIUM: 4096,
    LEVEL_HIGH: 8192,
}

# Anthropic requires ``budget_tokens`` < max_tokens, and a temperature alongside
# thinking. ``stream_anthropic`` hardcodes max_tokens=4096, so the top budget
# above cannot be honoured without also raising that cap; the resolved body
# bumps max_tokens when a budget needs the headroom. These are the knobs the
# admin config can override.
ANTHROPIC_MIN_BUDGET = 1024

# ── Model id heuristics ───────────────────────────────────────
#
# Provider model lists are plain strings with no capability metadata, so the
# only signal available is the id. These are substring tests over the lowercased
# id, which is how the id is normally written (``openrouter/anthropic/claude-
# sonnet-4`` → ``anthropic/claude-sonnet-4``).

# OpenAI: the o-series and gpt-5 and later take reasoning_effort. The regex
# matches `gpt-5`, `gpt-5.1` and the `gpt-6-*` line without also catching a
# hypothetical `gpt-50`; the earlier `("gpt-5")` substring test matched the
# o-series and gpt-5 but silently missed every gpt-6 model id.
_OPENAI_REASONING = re.compile(r"(^|[^a-z0-9])(o[134]|gpt-[5-9])")

# Anthropic extended thinking. 3.7+ is the only line that takes it reliably.
_ANTHROPIC_THINKING = ("claude-opus-4", "claude-sonnet-4", "claude-3-7", "claude-3.7", "claude-haiku-4")

# OpenRouter normalises every model behind one extra_body key, and exposes named
# levels for the models it proxies, including Anthropic's.
_OPENROUTER = ("openrouter.ai",)

# Vendor namespaces on an OpenRouter id (`openrouter/<vendor>/<model>`) whose
# models take a reasoning-effort level. OpenRouter's `reasoning` key is accepted
# for any of them, but not for every model it serves: one base_url also lists
# llama-guard (a classifier), llama-3.x-instruct and other plain models that have
# no effort knob, so treating the whole connection as capable put the dropdown
# on ~470 models, most of which would reject the parameter.
#
# Deliberately conservative — an unlisted vendor gets no dropdown, which is the
# safe default, and an admin can declare `effort_levels` for any model id.
_OPENROUTER_REASONING_VENDORS = frozenset(
    {
        "openai",
        "~openai",
        "anthropic",
        "~anthropic",
        "google",
        "x-ai",
        "deepseek",
        "~deepseek",
        "z-ai",
        "qwen",
        "moonshotai",
        "minimax",
        "bytedance-seed",
        "inclusionai",
        "xiaomi",
        "perplexity",
        "mistralai",
    }
)

# Gemini: "thinking_level" on the native API, but the OpenAI-compat shim takes
# ``reasoning_effort`` and maps minimal/low → low, medium → medium, high → high.
_GEMINI = ("generativelanguage.googleapis.com",)

# DeepSeek's reasoner models are always-on; there is no knob to turn.
_DEEPSEEK_REASONER = ("deepseek-reasoner", "deepseek-r1", "deepseek-r2")

# Model ids that do not generate text, so they take no reasoning-effort knob even
# when they arrive through a gateway that also serves models which do. A
# connection is not evidence: one OpenRouter base_url serves both
# ``anthropic/claude-opus-5.5`` and ``x-ai/grok-imagine-image``, and only the
# first can reason.
_NON_TEXT_MARKERS = (
    "imagine",
    "-image",
    "image-",
    "video",
    "audio",
    "speech",
    "whisper",
    "tts",
    "embed",
    "moderation",
    "rerank",
    "transcribe",
)


def _lower(value: str) -> str:
    return (value or "").strip().lower()


def is_agent_model(model_id: str) -> bool:
    """Agent profiles (codex, claude_code, …) run a CLI, not an HTTP body."""
    return "agent:" in (model_id or "") or _lower(model_id).startswith("agent/")


def _has_id_marker(model_id: str, markers: tuple[str, ...] | re.Pattern) -> bool:
    lowered = _lower(model_id)
    if not lowered:
        return False
    if isinstance(markers, re.Pattern):
        return bool(markers.search(lowered))
    return any(marker in lowered for marker in markers)


def is_non_text_model(model_id: str) -> bool:
    """True for image/video/audio/embedding endpoints, which cannot reason."""
    return _has_id_marker(model_id, _NON_TEXT_MARKERS)


# ── Provider-published capability metadata ────────────────────
#
# A model list is plain ids with no capability flags, so guessing from the id is
# the only option for most providers. OpenRouter is the exception: its /models
# response carries ``supported_parameters`` per model, which is authoritative
# and beats any id heuristic. Where that data has been fetched we use it, and
# the heuristic below is only the fallback for when it is absent.

# `{model id: "reasoning" in supported_parameters}` from OpenRouter's /models.
OPENROUTER_REASONING_MODELS: set[str] = set()


def known_reasoning_models() -> frozenset[str]:
    """Model ids with confirmed reasoning support, by provider."""
    return frozenset(OPENROUTER_REASONING_MODELS)


def set_openrouter_reasoning_models(ids) -> None:
    """Record which OpenRouter models publish ``reasoning`` support.

    An empty/``None`` input clears the set, which puts the heuristic back in
    charge — deliberately, so a failed fetch degrades to guessing rather than
    to "nothing takes effort at all".
    """
    global OPENROUTER_REASONING_MODELS
    OPENROUTER_REASONING_MODELS = frozenset(_lower(i) for i in ids or ())


def openrouter_model_takes_effort(model_id: str) -> bool | None:
    """True/False from OpenRouter's metadata, or ``None`` when it knows nothing.

    Ids arrive here in two shapes: the ``<vendor>/<model>`` form OpenRouter
    publishes, and cptr's connection-prefixed ``<prefix>/<vendor>/<model>``
    (a connection with ``prefix_id: "openrouter"`` prepends one segment). Both
    are matched, because which one arrives depends on the connection's
    ``prefix_id`` rather than on the caller.
    """
    if not OPENROUTER_REASONING_MODELS:
        return None
    lowered = _lower(model_id)
    if lowered in OPENROUTER_REASONING_MODELS:
        return True
    # Drop one leading segment if that is what makes it match. Taking the last
    # two segments instead would be wrong: it mangles ids that legitimately
    # carry a vendor segment (`openai/gpt-6-sol`) into a bare model name.
    parts = lowered.split("/")
    for start in (1, 2):
        candidate = "/".join(parts[start:])
        if candidate and candidate in OPENROUTER_REASONING_MODELS:
            return True
    return False


def _openrouter_vendor(model_id: str) -> str | None:
    """The ``<vendor>`` segment of an ``openrouter/<vendor>/<model>`` id.

    ``None`` when the id is not namespaced that way — a bare id on an
    OpenRouter connection is an unprefixed local alias, not a proxied model, so
    it gets no levels rather than being assumed capable.
    """
    parts = [part for part in _lower(model_id).split("/") if part]
    if len(parts) >= 3 and parts[0] in ("openrouter", "openrouter.ai"):
        return parts[1]
    return None


def _is_gpt5_family(model_id: str) -> bool:
    """``gpt-5`` and ``gpt-5.1`` but not ``gpt-5-nano``'s cheaper siblings."""
    return bool(re.search(r"(^|[^a-z0-9])gpt-5([^0-9a-z]|$)", _lower(model_id)))


def detect_effort_shape(connection: dict[str, Any] | None, model_id: str) -> str | None:
    """Return the wire shape for this model, or ``None`` when it takes no effort.

    An unrecognised model returns ``None`` rather than an optimistic guess: the
    caller hides the dropdown, because offering levels a model cannot accept is
    a request the provider will reject.
    """
    conn = connection or {}
    base_url = _lower(conn.get("base_url") or "")
    provider = _lower(conn.get("provider") or "")
    runtime = _lower(model_id)

    if _has_id_marker(runtime, _DEEPSEEK_REASONER):
        return "none"

    if is_non_text_model(runtime):
        return "none"

    # Anthropic's own API takes a token budget under ``thinking``.
    if provider == "anthropic":
        return "anthropic" if _has_id_marker(runtime, _ANTHROPIC_THINKING) else "none"

    if base_url and any(host in base_url for host in _OPENROUTER):
        # OpenRouter proxies many providers behind `reasoning`, so prefer the
        # nested key over the flat one even for the OpenAI models it proxies.
        #
        # Capability comes from OpenRouter's own per-model metadata when we have
        # it. A vendor list cannot stand in: 18 of OpenRouter's vendors are mixed
        # (openai is 76 models with reasoning support and 26 without, so
        # `openai/gpt-4o-mini` would 400 while `openai/gpt-6-sol` is fine), which
        # is why the list is only the pre-fetch fallback.
        published = openrouter_model_takes_effort(runtime)
        if published is None:
            vendor = _openrouter_vendor(runtime)
            if vendor is None or vendor not in _OPENROUTER_REASONING_VENDORS:
                return "none"
        elif not published:
            return "none"
        return "openrouter"

    if base_url and any(host in base_url for host in _GEMINI):
        return "gemini"

    if _has_id_marker(runtime, _ANTHROPIC_THINKING) and "anthropic" in base_url:
        return "anthropic"

    if _has_id_marker(runtime, _OPENAI_REASONING) or _is_gpt5_family(runtime):
        return "openai"
    return "none"


# ── Resolution ────────────────────────────────────────────────

# chat.models entry keys that are not effort configuration.
_NON_EFFORT_CONFIG_KEYS = frozenset(
    {
        "is_active",
        "params",
        "system_prompt",
        "compact_token_threshold",
        "builtin_tools",
        "request_params",
    }
)


def _config_entry(chat_models_config: dict[str, Any] | None, model_id: str) -> dict[str, Any]:
    """The most specific ``chat.models`` entry: model id, then ``"*"``."""
    config = chat_models_config or {}
    for key in (model_id, "*"):
        entry = config.get(key)
        if isinstance(entry, dict):
            return entry
    return {}


def _config_levels(entry: dict[str, Any]) -> list[str] | None:
    """Explicit ``effort_levels`` from config.

    ``None`` means "not declared, use the heuristic". An **empty** list means
    the admin explicitly said this model takes no effort knob, so it is
    returned as ``[]`` rather than being folded into the unset case — otherwise
    there would be no way to turn the dropdown off for a model the heuristic
    would otherwise guess at.
    """
    raw = entry.get("effort_levels")
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        return None
    levels = {str(level).strip().lower() for level in raw}
    levels = {level for level in levels if level in NAMED_LEVELS}
    if not levels:
        return []
    # Always ascending and de-duplicated, whatever order config listed them in.
    return [level for level in NAMED_LEVELS if level in levels]


def _config_param(entry: dict[str, Any], shape: str | None) -> str | None:
    """An explicit wire key from config, so an unknown model stays reachable."""
    raw = entry.get("reasoning_effort_param")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def resolve_effort_levels(
    model_id: str,
    connection: dict[str, Any] | None = None,
    chat_models_config: dict[str, Any] | None = None,
) -> list[str]:
    """The levels this model accepts, as a list. Empty means "no dropdown".

    Always begins with ``"default"`` — the no-op level that writes nothing to
    the request.
    """
    if is_agent_model(model_id):
        return [LEVEL_DEFAULT]

    entry = _config_entry(chat_models_config, model_id)
    shape = detect_effort_shape(connection, model_id)

    # Config is consulted before the heuristics, so an admin who declared
    # `effort_levels` for a model outranks what we infer from its name — including
    # for a model whose id says it cannot reason.
    levels = _config_levels(entry)
    if levels is not None:
        return [LEVEL_DEFAULT] if not levels else [LEVEL_DEFAULT, *levels]

    # An explicit param key is the escape hatch for a model the heuristics know
    # nothing about; declaring the wire key is itself the statement that the
    # model takes one.
    if shape == "none" and _config_param(entry, shape):
        shape = "openai"

    if shape == "none":
        # Either the id told us the model cannot reason, or nothing told us
        # anything. The honest answer for an unknown model is "we do not know",
        # and the safe one is to offer nothing: the first version offered the
        # full level list for every unrecognised id, which put the dropdown on
        # all ~500 models in the live config — image generators, embedding
        # endpoints and plain instruct models included — where picking a level is
        # a guaranteed provider rejection. A model stays knobless until its id
        # matches or an admin declares `effort_levels` for it on the admin Models
        # page.
        return [LEVEL_DEFAULT]

    return [LEVEL_DEFAULT, *NAMED_LEVELS]


def effort_request_param(
    level: str,
    model_id: str,
    connection: dict[str, Any] | None = None,
    chat_models_config: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any] | None:
    """Turn a named level into the body fragment the provider expects.

    Returns ``None`` for ``"default"`` (and anything unrecognised), which the
    caller drops rather than sending — so an unknown or unset level can never
    reach the provider.
    """
    level = (level or "").strip().lower()
    if not level or level == LEVEL_DEFAULT:
        return None

    entry = _config_entry(chat_models_config, model_id)
    shape = detect_effort_shape(connection, model_id)
    param_key = _config_param(entry, shape)

    # ── Anthropic: a token budget, plus the max_tokens headroom it needs ──
    if shape == "anthropic" and not param_key:
        budget = entry.get("anthropic_thinking_budget")
        if not isinstance(budget, int) or budget <= 0:
            budget = ANTHROPIC_BUDGETS.get(level)
        if not budget:
            return None

        # stream_anthropic hardcodes max_tokens=4096. A budget must stay below
        # it or Anthropic 400s, so raise the cap when the level needs it.
        cap = max(4096, budget + 1024)
        body: dict[str, Any] = {"thinking": {"type": "enabled", "budget_tokens": budget}}
        if max_tokens is None or max_tokens < cap:
            body["max_tokens"] = cap
        return body

    if level not in NAMED_LEVELS:
        return None

    # ── Everything else is a named level on a documented key ──
    if shape == "openrouter":
        # OpenRouter takes both spellings depending on the proxied provider;
        # ``reasoning`` is the one that is accepted by Anthropic models too.
        return {PARAM_NESTED: {"effort": level}}

    if shape == "gemini":
        # The OpenAI-compat shim maps minimal/low onto its own low level.
        value = "low" if level in (LEVEL_MINIMAL, LEVEL_LOW) else level
        return {PARAM_OPENAI: value}

    if param_key:
        return {param_key: level}

    return {PARAM_OPENAI: level}


def effort_request_params(
    level: str,
    model_id: str,
    connection: dict[str, Any] | None = None,
    chat_models_config: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """``effort_request_param`` merged over an existing ``request_params`` dict.

    The chat's own ``request_params`` wins over the per-model/global admin
    defaults but *loses* to the effort dropdown: it is the more immediate,
    per-conversation control, and a hand-typed ``reasoning_effort`` in the plus
    menu should not silently outrank what the user just picked here. Where both
    set the same key the dropdown's value is written last.
    """
    from_body = effort_request_param(
        level, model_id, connection, chat_models_config, max_tokens
    )
    if not from_body:
        return {}
    return dict(from_body)