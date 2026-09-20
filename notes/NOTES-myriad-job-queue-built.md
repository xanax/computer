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

- **No UI beyond the dashboard board** (see §8). Deferral and review are reachable from the
  board, from chat (the `defer_workspace_todo` tool) or from the API. There is still no
  dedicated review queue *page* — a `needs_review` row is a marked row on the board, and the
  design's §11 open question ("where does a review queue live") is answered only by that
  strip for now.
- **Phases 3-4:** pricing, `trigger='window'`, `resource` serialization, retry policy,
  worktrees. `rrule`/`resource` columns exist and are unused; nothing in the UI sets `rrule`.
- **Timers and automations are not folded in.** They still run their own loops; `jobs` is
  where they would go, but Phase 1-2 deliberately did not move them.

## 8. The board lands in the dashboard

The dashboard's "To do" list *is* the board now: it renders `/api/todos` and `/api/jobs`
merged, because a job the human checked off in the todo shim and a job an agent is
mid-way through are the same table read two ways.

- Rows arrive from both sources and are deduped by id (`Row.fromTodos`), so a todo-backed
  job cannot draw twice; jobs with no todo of their own ("extra" rows) are appended after
  the human's list.
- A row shows a second line only when it has something to say: `waiting to run`,
  `scheduled · Sep 20, 10:11 PM · in 1 hr · deepseek-flash`, `running now`, `ready to
  check`, `stopped`, plus `attempt N` and the error text for a failed run. A plain todo
  keeps its single line.
- `needs_review` gets an **ink attention strip** (mono palettes) — solid ink on the row's
  edge, no wash, no dithering — because "a model did something and nobody has looked" is
  the one state you must not have to hunt for.
- Per-row actions: the check toggles the todo (or the job, for extra rows), `Run later…`
  opens an inline form (when + model + optional extra instructions; **Schedule** stays
  disabled until a time is typed), a chat bubble opens the run's own chat, and the ×
  clears a pending or finished run. While a run is in flight the check slot becomes a
  **stop** button instead.
- **Units are a trap the board had to fix:** `jobs.trigger_at` is nanoseconds while
  `created_at`/`updated_at` are milliseconds. The first render read it as ms and printed
  `in 20716777011 days`; `nsToMs()` is now applied at the point of use.

### A cancel can beat the run to the starting line (fixed)

Live probe, 2026-09-20: pressing **stop** on a running row 25 ms after it appeared left the
row reading `stopped` — and the model went on to work for another two minutes.

`run_job` writes `running` *before* it calls `start_task`, and `/cancel` can only cancel
tasks already in `chat_task._tasks`. A cancel landing in that gap found nothing to kill, so
it only flipped the row; the runner then started the turn anyway. `run_job` now re-reads the
row as late as possible before `start_task` and, if it is no longer `running`, finalizes the
run's assistant message as `cancelled` (so the chat does not dangle as a pending turn) and
returns without starting anything. `test_a_cancel_before_the_turn_starts_never_runs` fails
without the re-read. Ledger: B-012.

### Verified

CDP probes against the live server (`.cptr/harness/cdp.mjs`, `probe-*.js` in the same
folder), not screenshots by eye:

- a deferral typed into the board's own form (`test`, 1 minute, `deepseek-flash`) fired
  60 s later, ran in its own chat — the brief, then `ok.` — and settled the row at
  `ready to check · Sep 20, 08:08 PM · deepseek-flash` with the attention strip;
- a run genuinely observed `running now` with the stop button present, and taking it moved
  the row to `stopped` (`cancelled` in the DB);
- the trash button on a job-backed row removes **both** the mirror todo and the job
  (`/api/jobs` and `/api/todos` both empty of it afterwards);
- the model picker really offers the 22 chat models with `deepseek-flash` preselected;
- the board's own **Run later…** form: opens under the row, offers all 22 models with
  `deepseek-flash` preselected, keeps **Schedule** disabled while the when field is empty
  and enables it on the first keystroke;
- the whole loop driven from the UI, timed from the API side (`test`-style probe todo,
  `1m`, `deepseek-flash`, board form only):
  `20:40:03 open` → `20:40:53 running` (attempt 1, `run_chat_id` + `run_started_at` in
  `meta`) → `20:42:14 needs_review`, with the run's own chat holding the brief
  (`[Scheduled job — a deferred workspace todo just fired] …`) and the model's reply
  (`done=True`) — i.e. schedule → poll → run → read-back → land for review, none of it
  through the API by hand;
- the settled row then reads `ready to check · Sep 20, 08:40 PM · deepseek-flash` and
  carries **Open the run's chat**, **Run later…**, **Clear this finished run** and
  **Remove**, i.e. a finished run can be re-scheduled or cleared without leaving the board;
- the run's own proposal shows up as the dashboard's `Complete: … Approve/Reject` row, and
  removing the todo cleared the pending request too — after the B-013 fix below.

### A proposal can outlive its todo (fixed)

The probe above also produced a live **B-013**: the run proposed completing its own todo, the
todo was then removed, and the pending `todo_requests` row stayed `pending` — the dashboard
kept drawing a `Complete: …` row with a working-looking **Approve** for a todo that no longer
existed (`Job.update_status` on a missing id is a no-op that still answers `ok`). `list_pending`
does not join against `jobs`, so nothing hid it. Fixed in
`TodoRequest.resolve_for_todo()` + `Job.delete` / `toggle_todo`; see [`BUGS.md`](../BUGS.md).

### Probe traps: two ways a click proves nothing

The verify pass cost three false readings before the board's own form was proven. Both causes
are silent — no exception, no console error, just a probe that reports the feature broken.

1. **Document-wide selectors pick up another dashboard's form.** `probe-defer-e2e.js` looked
   up `.icon-btn[aria-label*="later"]`, `.defer-at` and `.defer-form button` across the whole
   document and clicked the first match. With more than one workspace mounted, that is the
   *other* workspace's dashboard: the probe opened, filled and submitted a form belonging to a
   row it never seeded, the `POST` went to that workspace's job, and `/api/jobs?workspace=` —
   the only thing it checked — showed no deferral. Fixed by scoping every lookup to the row
   the probe seeded (`rowFor('probe: …')` → that row's own buttons, `form = li > .defer-form`).
2. **A click on a still-`disabled` button is silently ignored.** Svelte updates
   `disabled={deferBusy || !deferAt.trim() || !deferModel}` on a microtask, so for one tick
   after the `input` event the Schedule button is still disabled; `.click()` in that same tick
   does nothing at all (`fetch` never called, `deferError` null, form still open). The probe
   now polls `!btn.disabled` (20 × 50 ms) before clicking and reports
   `enabledAtFirstLook`/`enabled` so the difference is visible in the output. The same trap
   applies to any element a probe drives immediately after setting its bound value.

With both fixed the probe is deterministic: `formClosed: true`, row `scheduled · Sep 20,
08:49 PM · now · deepseek-flash`, job `trigger: "at"` with `trigger_at` set. **A probe that
has to prove the board works must confine its selectors to the row it seeded and wait for the
control to become enabled — a click proves nothing unless you can show the request it made.**
