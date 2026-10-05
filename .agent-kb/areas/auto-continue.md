---
area: auto-continue
title: Auto-continue mode — the server re-prompts a finished chat
aliases: [auto continue, iterate mode, self-follow-up, queued follow-up, nothing left to iterate]
updated: 2026-10-06
---

# Auto-continue

A per-chat mode: when a turn of that chat ends **cleanly**, the server appends a
static follow-up user prompt so the agent starts another round on its own.

## Map
- `cptr/utils/auto_continue.py` — the whole decision (`maybe_queue_auto_continue`),
  the run counter, the stop-phrase match.
- `cptr/utils/chat_task.py` — hook at the `task_completed_success = True` line in
  the `if not restart:` tail of the agentic loop (was ~2997 before the `effort`
  work shifted lines). Reached only on success.
- `cptr/utils/chat_task.py:588` `_is_pending_chat_input` — what makes the written
  message *self-processing*: `meta.queued` is enough.
- `cptr/utils/chat_task.py:608` `_pending_input_ready` — the ready gate.
- `cptr/utils/chat_task.py:644` `process_pending_chat_inputs`, called from the
  task's `finally` — picks the follow up and starts the next turn.
- `cptr/routers/chat.py` — `reset_runs` on settings save and on user send.
- `cptr/frontend/src/lib/components/chat/PlusMenu.svelte` — the toggle + panel.
- `scripts/add-auto-continue-locales.py` — writes the 5 `plusMenu.autoContinue*`
  keys into all ten locales. `--check` to verify only.

## Facts
- [verified 2026-10-06] **No new scheduler was needed.** Writing the follow-up as
  a `meta: {"queued": true}` user message is sufficient: the finished task's
  `finally` block already calls `process_pending_chat_inputs`, which finds it and
  starts the next turn. The queued-message UI then renders it, so the user can
  read, edit, force-send or delete it like any other queued prompt.
- [verified 2026-10-06] `_pending_input_ready` only rejects a pending input whose
  parent assistant message is **not done**. The hook runs after
  `_save_message(..., done=True)`, so the gate passes by construction — but that
  ordering is load-bearing; moving the hook earlier would silently stall the chain.
- [verified 2026-10-06] Hook placement gets the "only a real completion counts"
  property for free: cancel, error, max-iterations, tool-approval and `ask_user`
  all exit the loop at *different* points and never reach the success tail.
- [verified 2026-10-06] `assistant_plain_text` must read `output` items, not just
  `content`. A tool-heavy turn leaves `content` empty, so checking `content`
  alone makes every such turn look silent and the loop never stops.
- [verified 2026-10-06] The stop phrase is matched against the **tail** 600 chars
  so a mid-answer aside doesn't end the chain, but a closing summary still does.
- [verified 2026-10-06] The run counter lives in `meta.auto_continue_state`, a
  sibling of `meta.params` — *not* inside `params`. The client rewrites `params`
  wholesale on every settings save and would erase a counter stored there.
- [verified 2026-10-06] Re-arming the budget needs three call sites (toggle on,
  settings edit, human send) or a chat that hit the cap can never auto-continue
  again without a trip through the settings round-trip.
- [verified 2026-10-06] Internal chats are excluded via `is_internal_chat`:
  re-prompting a subagent would fork agents forever.
- [ran 2026-10-06] `npx prettier --write` on ChatPanel/ChatInput/chat.ts also
  reformatted a concurrent `effort` feature's lines (blank line, comment
  reflow) — the two sets of edits coexist, but expect prettier to touch
  neighbours it did not write.

## Decisions
- Reuse the queued-input machinery rather than a new timer/queue: fewer moving
  parts, and the follow-up becomes user-visible and user-cancellable for free.
- Safety is layered rather than a single cap: only-success-exits, a run cap, the
  agent's own stop phrase, an internal-chat guard, and a "no stacking" guard.
- The follow-up is written with `meta.auto` so a second pass can see that one is
  already outstanding and decline rather than forking the chat into two racing
  branches.

## Dead ends
- Do not look for a place to schedule a delayed re-send; `process_pending_chat_inputs`
  in the task `finally` already covers the whole loop.

## Open
- Per [silent-turn-truncation](silent-turn-truncation.md) (B-023), a turn
  truncated by the provider (`finish_reason: "length"`) is recorded identically
  to a genuine completion, so **auto-continue cannot currently tell them apart**
  and will happily iterate on a truncated turn. Fixing that means teaching the
  `done` handler to capture `finish_reason`; unaddressed here.
- Whether the chain should also stop when a turn's only output is tool calls with
  no prose at all (no signal to judge, so currently it keeps going).

## Do not redo
- The 10 locale files are edited by **text append**, not a JSON round-trip;
  `json.loads` on the patched text must be asserted, which is what caught a
  trailing-comma bug before it hit a real file.