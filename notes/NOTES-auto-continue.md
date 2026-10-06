# Auto-continue mode

A per-chat composer toggle (`+` menu → **Auto-continue**) that makes cptr keep
working on a conversation without you.

When a turn of that chat ends **cleanly**, the server appends a static follow-up
prompt and the agent starts another round.

## How it actually works

There is no scheduler and no new queue. The follow-up is written as a user
message with `meta: {"queued": true, "auto": true}`, which is exactly the shape
the existing queued-input machinery already looks for:

1. The turn finishes at the `task_completed_success = True` line in
   `cptr/utils/chat_task.py`.
2. `maybe_queue_auto_continue` decides whether to append a follow-up and writes
   it as a queued message parented to the finished assistant message.
3. The task's `finally` block calls `process_pending_chat_inputs`, which finds
   the message, promotes it to a real user message, and starts the next turn.

The follow-up therefore shows up in the transcript as a queued message you can
read, edit, send immediately, or delete. Turning the toggle off, or deleting the
queued message, stops the chain.

## Stopping

Every exit is guarded, because a loop that re-prompts itself never ends on its own:

| Guard | Behaviour |
| --- | --- |
| Only success queues | Cancel, error, iteration cap, tool-approval waits and `ask_user` all exit the loop elsewhere and never reach the hook. |
| Run cap | `meta.auto_continue_state.runs`, default 25, ceiling 200. Re-armed when you toggle it on, edit its settings, or send a real prompt. |
| Agent's own stop phrase | The prompt asks for a bare `nothing left to iterate` when it is done; matched against the tail 600 chars. |
| Internal chats | Subagent/harness chats never auto-continue. |
| No stacking | A second follow-up is declined while one is already outstanding. |

## Settings

Per chat, stored in `meta.params.auto_continue` alongside `plan_mode`:

- `enabled` — the toggle.
- `prompt` — your own follow-up text. Empty uses the built-in default.
- `max_runs` — cap on consecutive follow-ups.

The run counter deliberately sits in `meta.auto_continue_state`, *outside*
`meta.params`, because the client rewrites `params` wholesale on every settings
save and would erase it.

## Truncated turns

Fixed (B-023 / B-027). The three stream adapters now carry the provider's stop
reason on their terminal `done`, and `maybe_queue_auto_continue` declines to
queue when it means the answer was cut short (`length`, `max_tokens`,
`max_output_tokens`, `content_filter`, `refusal`, `incomplete`) — see
`is_truncated` / `TRUNCATION_FINISH_REASONS` in `cptr/utils/auto_continue.py`.

Two decisions worth keeping:

- **`None` means clean.** The agent-target path never reports a reason, so
  treating "unknown" as truncated would silently disable the feature on that
  transport for every turn. Only a *recognised* truncation reason gates.
- **A declined turn does not advance the run counter.** Otherwise repeated
  truncations would exhaust the cap of a chat that never actually iterated.

A truncation is logged (`turn ended with finish_reason=…`). Surfacing it in the
chat itself (a badge on the message) is not done.

## Known limitation

Nothing outstanding on the truncation front. `NOTES-silent-turn-truncation.md`
keeps the original evidence for why the fix looks the way it does; its line
numbers and "no fix applied" notes are historical.