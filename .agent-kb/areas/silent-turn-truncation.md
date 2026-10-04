---
area: silent-turn-truncation
title: Why a chat turn stops with no error
aliases: [finish_reason, truncated turn, stops for no reason, B-023]
updated: 2026-10-04
---

# Silent turn truncation

## Map
- `cptr/utils/ai.py` — the three stream adapters. Only the request body is logged (`log_upstream_request`); the provider's **response** is never captured anywhere.
- `cptr/utils/chat_task.py:2567` — the `done` event handler. This is where the stop signal is thrown away.
- `cptr/utils/chat_task.py:2933` — the SSE/gateway re-emit, which **hardcodes** `finish_reason: "stop"`.
- `cptr/env.py:90` — `CHAT_MAX_ITERATIONS`, default 2048.

## Facts
- [ran 2026-10-04 chat `457af6a2` task `d519b071`] A turn that "stops for no reason" ends through the normal `done` path at `chat_task.py:2587` with `_save_message("done", …)`, which writes `done=1` and **no** `meta`. That is why there is no error to see: the truncation is recorded identically to a genuine completion.
- [verified 2026-10-04 `cptr/utils/ai.py:798`] The completions adapter reads `finish_reason` only to detect `"tool_calls"`. Every other value — `length`, `content_filter`, `stop` — is discarded, and the adapter then unconditionally yields `{"type": "done"}` (`ai.py:846`).
- [verified 2026-10-04 `cptr/utils/chat_task.py:2567`] The `done` handler never reads `event["finish_reason"]`, so a `length` stop is indistinguishable from a real end-of-turn.
- [verified 2026-10-04 `cptr/utils/ai.py:1163`] The Responses adapter handles `response.completed` but has **no** `response.incomplete` branch, so that truncation signal would be dropped too.
- [verified 2026-10-04 `cptr/utils/chat_task.py:1589`] `finish_reason` is *hardcoded* to `"stop"` when re-emitting to the gateway queue, so the SSE path cannot report a truncation even if the stream carried one.
- [ran 2026-10-04 chat `457af6a2`] Not a compaction artefact: compaction runs at the **top** of the iteration loop (`chat_task.py:2335`), and the log shows `compacted:` at 12:15:30 followed by a full extra iteration of work.
- [ran 2026-10-04 chat `457af6a2`] Not the iteration cap, cancellation, restart, or a crash: only ~19 of 2048 iterations ran, save reason was `done` (not `cancelled`/`max iterations`/`error`), and no restart or exception occurred in the window.
- [ran 2026-10-04 all 984 `done=1` assistant messages] The signature — last output item is a text message that *announces* an action and then stops — appears in **2** messages, one of which is this chat. So it is rare but not unique to this run.
- [ran 2026-10-04] `meta` is stored as the **string** `'null'`, so `WHERE meta IS NULL` returns 0 rows. Filter on the stored value, not SQL NULL.
- [ran 2026-10-04 chat `457af6a2`] `usage.output_tokens` was 442 with `input_tokens` 67482 — a single short final turn. The prompt had just been compacted, so the model's next call had ample context; size was not the constraint.

## Built
- Nothing yet. This is a diagnosis only; no fix is applied.

## Decisions
- Diagnose first, do not patch silently. A truncation guard is a behaviour change (auto-continuing a turn) and needs the user's call on whether to auto-resume or just surface the reason.

## Dead ends
- `chat.list.task_state_anomaly` warnings for this chat are unrelated noise — they report unfinished *task-list* entries (5 tasks, 1 in_progress + 4 pending), not the turn stopping.
- `grep cptr-start.log` without `-a` reports "binary file matches" and prints nothing; the log contains NUL bytes from the earlier repair work. Always use `grep -a`.

## Do not redo
- The stop is provable from `chat_messages.output` alone: if the last item is a `message` and the turn is `done=1` with no `meta.error`, the turn was truncated by the provider. No server-log spelunking needed.

## Open
- Whether to auto-continue on `finish_reason: "length"` or surface it as a visible notice.
- Whether `log_upstream_request` should gain a response counterpart, since without it a truncation can never be attributed to a provider.