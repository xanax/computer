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

## Three clean exits, not one (fixed)

The first cut wired the hook into a single `task_completed_success = True` site
and called it "the only exit that counts". There are **three**, one per
transport, and each ends the turn its own way:

| Site | Transport |
| --- | --- |
| `_run_agent_target` (~line 2052) | agent target / SDK |
| inside the `event["type"] == "done"` branch (~2692) | Responses / chat-completions stream |
| after the iteration loop (~3029) | collected-tool-calls path |

Consequence of the miss: auto-continue was **silently dead on two of the three
providers** — no error, no log, the chat just stopped iterating depending on
which transport you happened to use. All three now call one shared
`_auto_continue_after_turn(finish_reason)` closure defined next to
`_save_message`; only the stream site has a real reason to pass, so the other
two pass nothing and that is correct.

- Grep for `task_completed_success = True` (there are exactly 3) whenever you
  touch completion. A fourth exit appearing later must get the hook too.
- `tests/test_auto_continue_wiring.py` asserts the invariant structurally (AST,
  not behaviour): each clean exit is within 4 lines of a helper call. Behavioural
  tests cannot see this, because the failure mode is a *missing* call site.
- Verify the guard bites by deleting one hook and re-running; it fails with
  `assert not [2051]`.
- The helper swallows its own exceptions, so a broken follow-up can never
  disturb a turn that already succeeded.

## Dead ends
- Do not look for a place to schedule a delayed re-send; `process_pending_chat_inputs`
  in the task `finally` already covers the whole loop.

## Truncated turns (B-023 → B-027, fixed)

Auto-continue reads the *success* signal, and that signal was ambiguous: the
provider's `finish_reason` was read only to detect `tool_calls` and then thrown
away, so a turn cut off at `length` reached the hook looking exactly like a
finished one and the chain re-prompted onto a half-answer until the run cap.

Where the stop reason hides, per transport — it is **not** uniformly on the
terminal event:

| Adapter | Stop reason lives on |
| --- | --- |
| `stream_anthropic` | `message_delta` → `delta.stop_reason` (`message_stop` carries **nothing**; reading it there yields `None` forever) |
| `stream_openai_completions` | each chunk's `finish_reason`; the *last non-empty* one wins, so record it unconditionally, never only for `tool_calls` |
| `stream_openai_responses` | `response.incomplete` → `incomplete_details.reason`. There was **no** `incomplete` branch at all before this fix, so a truncated response ended the stream with no `done`. A `response.completed` that still carries `incomplete_details` counts as truncated too. |

All three now yield `{"type": "done", "finish_reason": ...}`; `chat_task` passes
it to the helper, logs a warning when truncated, and `auto_continue.is_truncated`
declines to queue. `gateway._collect` returns the reason instead of a hardcoded
`"stop"` so OpenAI-compatible clients can see it.

- `None` is deliberately **not** truncation: the agent-target path reports no
  reason, and guessing "unknown = truncated" would disable auto-continue there
  for every turn — a worse bug than the one being fixed.
- A declined truncated turn must **not** advance the run counter, or repeated
  truncations silently exhaust the cap of a chat that never iterated.
- The parent bug stays open upstream: the fork can carry the signal, not stop the
  provider truncating. See B-023 in `BUGS.md`.

## Open
- Whether the chain should also stop when a turn's only output is tool calls with
  no prose at all (no signal to judge, so currently it keeps going).
- Whether truncation should be surfaced *in the chat* (a badge on the message),
  not just in the log. It is visible to API clients and to the log today.

## Do not redo
- The 10 locale files are edited by **text append**, not a JSON round-trip;
  `json.loads` on the patched text must be asserted, which is what caught a
  trailing-comma bug before it hit a real file.
- Don't trust a passing test suite as proof of coverage. The queueing decision
  shipped with 25 green tests that were **all pure-helper tests**; nothing
  exercised `maybe_queue_auto_continue` against a DB. `tests/test_auto_continue_db.py`
  now does, and `scripts/mutate-auto-continue.py` proves it bites (7/7 mutants
  killed: run cap, cap off-by-one, stacking, internal-chat, stop phrase, enabled
  flag, custom prompt). Re-run the mutator after touching any guard.
- `Chat.update_meta` defaults to `updated_at=0`, which **zeroes** the column
  rather than leaving it. Any state write must pass `now_ms()`. Guarded by
  `test_reset_does_not_zero_updated_at`.
- `_write_state` merges into existing `meta`; it must not replace it, or a
  queued prompt drops `params`, `last_model` and anything else the chat holds.