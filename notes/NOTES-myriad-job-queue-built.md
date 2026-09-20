# NOTES — Myriad job queue, Phases 1-2 as built

Companion to [`NOTES-myriad-job-queue.md`](NOTES-myriad-job-queue.md), which is the *design*
(and still the reference for Phases 3-4). This is the record of what actually landed, what
it cost, what was deliberately left out, and how it was verified.

Status: **built and verified.** Phases 1 and 2 of the design note. No Phase 3/4 work.

---

## 1. What is in the tree

| File | What it is |
|---|---|
| `cptr/migrations/versions/0009_create_jobs.py` | `jobs` table, 3 indexes, backfill from `workspace_todos`. No drops. `downgrade()` drops `jobs` and leaves `workspace_todos` alone. |
| `cptr/models/jobs.py` | `Job` + constants; `get_by_id`, `list_for_workspace`, `create`, `update_by_id`, `update_status`, `mark_due_queued`, `claim_due`, `list_running`, `count_by_status`. |
| `cptr/utils/jobs.py` | The scheduler: `job_worker_loop`, `run_job`, `_prepare_run`, `_watch_run`, `recover_jobs`, `build_prompt`. |
| `cptr/routers/jobs.py` | `GET/POST /api/jobs`, `GET/PATCH/DELETE /api/jobs/{id}`, `POST /{id}/defer`, `POST /{id}/cancel`. |
| `cptr/routers/todos.py` | Rewritten as a shim over `jobs`. Response shape unchanged. |
| `cptr/socket/main.py` | `emit_jobs_changed` emits `jobs_changed` **and** `todos_changed`. |
| `cptr/utils/tools.py` | `defer_workspace_todo` tool (registry entry, group, and the allowed list all added). |
| `tests/test_jobs.py` | 31 tests: backfill, transitions, shim parity, API, the defer tool, the watcher, restart recovery, migration up/down. |
| `.cptr/harness/e2e_jobs.py` | Live E2E against a scratch instance: create → defer → watch → read the run's chat. |

`cptr/env.py` gained `JOB_POLL_INTERVAL` (default 2 s) and `JOB_TOOL_APPROVAL_MODE`
(default `full`). `cptr/app.py` includes the router, starts `job_worker_loop` in the
lifespan, cancels it on shutdown, and calls `recover_jobs()` at boot.

## 2. The shapes that matter

**Status machine.** `open → queued → running → needs_review | failed`, with `done` and
`cancelled` reachable only from a human route. `SETTLED_STATUSES` and `OPEN_STATUSES` are
constants, not string literals scattered around.

**Trigger.** Phase 2 implements exactly one: `trigger='at'` with `trigger_at` in ns (the
same unit `utils/timers.py` uses). `trigger='manual'` is what a human todo gets, so the
scheduler can never fire it. `rrule` and `window` are columns the design asked for, but
nothing reads them yet — Phases 3+.

**Executor.** `human` rows are never claimed (`claim_due` and `mark_due_queued` both filter
them out); a model id means "a run may execute this".

**Destination.** `parent_chat` set → the run is a turn in that conversation (timer
semantics). `parent_chat` null → a fresh chat titled after the job, with
`meta.workspace` and `meta.job_id` set and its `.cptr/chats/<id>.json` marker written, so
the sidebar and export paths find it (automations do the same).

**Claim safety.** Both transitions are a single conditional
`UPDATE … WHERE id IN (SELECT …) … RETURNING id`, so two pollers cannot double-fire a row.
This is the §10 risk-2 fix applied to the deferred lane; it is still not a real queue
(one worker per process, `attempts` unbounded), which is Phase 4's problem.

## 3. Phase 1 as a non-destructive shadow

`workspace_todos` is still written, in lockstep, by every shim route. That was the design's
call and it paid off: the built dashboard reads `/api/todos`, which now reads `jobs`, and
nothing in the frontend changed at all (`cptr/frontend/src/lib/apis/todos.ts` is untouched).

Two consequences worth knowing:

- A job the board holds that is *not* a human todo (`needs_review`, a deferral in flight)
  is deliberately **absent** from `/api/todos`. It is work in progress, not the human's
  checklist; `test_non_todo_jobs_stay_off_the_todo_list` pins that.
- Finishing a todo by hand *cancels* a pending deferral, because `done` is no longer
  `open`/`queued` and the scheduler only ever picks those up
  (`test_finishing_a_deferred_todo_cancels_the_run`).

Approval does not move: an agent that thinks the work is done calls
`complete_workspace_todo`, which creates a `todo_request` for the human exactly as before.
The shim owns `todo_requests`; `jobs` knows nothing about it.

## 4. Phase 2: the invariant, and what it costs

An unattended run ends `needs_review`, never `done`. The outcome is read back from the run's
own assistant message (`meta.error` → `failed`, otherwise `needs_review`), because
`run_chat_task` swallows its own exceptions and records them on the message rather than
raising.

Failure paths, all of which settle the row so nothing sits `running` for ever:

| Situation | Outcome |
|---|---|
| Model gone from config at fire time | `failed`, `last_error="model unavailable: …"` |
| `parent_chat` deleted | `failed`, `last_error="parent chat no longer exists"` |
| Parent chat mid-turn | **requeued** (`queued`) — nothing is wrong with the job |
| Run errored | `failed`, `last_error` from the message |
| Run message deleted | `failed`, `last_error="run message disappeared"` |
| Row unreadable 20 polls in a row | `failed`, `last_error="could not read the run's state"` |
| Run outlives 6 h | `failed`, `last_error="run did not finish within 21600s"` |
| Process restart mid-run | `failed`, `last_error="interrupted by restart"` (next boot) |
| Cancelled/retried/deleted during the run | left alone — the canceller owns the status |

Every settle emits `jobs_changed`, so an open dashboard moves without a reload.

## 5. The bug the live run found (and the tests did not)

The first end-to-end deferral stalled at `running`: the agent *had* answered, but the
watcher died on `NameError: STATUS_RUNNING is not defined` inside its `except Exception`
handler, which logged "could not read its run's message" and returned. Two lessons, both
now encoded:

1. `_watch_run` had no unit test — 25 passing tests said nothing about it. It has five now
   (clean settle, errored run, vanished message, cancelled job, `jobs_changed` emitted).
2. A watcher that gives up on the first read error strands the row. Read failures are now
   counted, retried, and bounded (`_MAX_READ_FAILURES = 20`), and *every* exit path settles
   the row. A watcher whose own code is broken can still fail a job — that is the correct
   direction to fail in.

## 6. Verification

    .venv/bin/python -m pytest tests/ -q          # 68 passed

Live, against a scratch instance (`cp -a ~/.cptr /tmp/jobse2e`, server on :4210):

    .venv/bin/python .cptr/harness/e2e_jobs.py

observed `open → running → needs_review` in ~40 s, with the run's chat holding the brief and
the model's answer (`hello from the queue`, 6 421 in / 255 out tokens), and the todo itself
still `open` — the invariant, end to end.

Against a **copy of the real database** (`cp -a ~/.cptr /tmp/reallike`, port 4211):

- `0009` applies on real data: 211 chats, 1 todo → 1 job, `trigger='manual'`,
  `meta.legacy="workspace_todos"`, `workspace_todos` untouched;
- the server boots on it (`health:200`, "Job scheduler started"), and the one real
  workspace's todo reads identically from `/api/todos` (shim) and `/api/jobs` (board);
- an orphaned `running` row from a killed process came back as
  `failed: interrupted by restart` on the next boot.

## 7. What is *not* built

- **No UI.** There is no deferral control, no review queue, no board view. A deferral is
  reachable only from chat (the `defer_workspace_todo` tool) or the API; a finished run is
  visible as its own chat in the sidebar plus a `needs_review` job row. If the agent
  proposes closing the todo, the proposal shows up in the existing dashboard. This is the
  honest gap: the design's §11 open question — where a review queue lives — is still open,
  and Phase 3-4's review backlog is the real cost of the queue.
- **Phases 3-4:** pricing, `trigger='window'`, `resource` serialization, retry policy,
  worktrees. `rrule`/`resource` columns exist and are unused.
- **Timers and automations are not folded in.** They still run their own loops; `jobs` is
  where they would go, but Phase 1-2 deliberately did not move them.
