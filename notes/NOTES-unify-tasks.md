# NOTES — One task store: todos, automations and timers → `jobs`

**Status: built.** Migration `0010_unify_tasks.py`, one scheduler
(`cptr/utils/task_scheduler.py`), one page (`/scheduled`), timers folded at boot.
Phases 1-2 of [`NOTES-myriad-job-queue.md`](NOTES-myriad-job-queue.md) are the
`jobs` table and the dashboard board; this document is design + as-built for the
phases that followed, and it is the "storage half" that `0010`'s docstring cites.

## 1. Where a task could live before

Four shapes, three mechanisms, none of which could see the others:

| was | created by | when it ran | status of a run |
|---|---|---|---|
| `workspace_todos` | the todo list | never — a human did it | `pending` / `done` |
| `automations` | the automations page | its own `automation_worker_loop`, every 15 s | — |
| `automation_runs` | that loop | fire and forget | `success` / `error` |
| dormant child chat + `meta.timer_at` | the `timer` tool | its own `timer_worker_loop`, every 1 s | the run existed only as chat messages |

So "what am I waiting on?" had four answers on four screens, two of them
invisible, and two of the four loops were polling the same database for the same
kind of event.

## 2. One row now says everything

`jobs` (migration `0009` + `0010`) carries every one of them. The columns that
do the work:

- `kind` — `task` (a thing to do, or a recurring *template*) vs `run` (one firing
  of a template, `parent_job` → the template).
- `executor` — `human`, or a model id. A todo nobody runs is `human`.
- `trigger` — `manual`, `at` (one shot, `trigger_at`), or `rrule` (recurring,
  `rrule` + `next_run_at`).
- `status` — `open`, `queued`, `running`, `needs_review`, `done`, `paused`,
  `failed`, `cancelled`. `paused` is how a recurring task is switched off without
  losing its schedule or its history: a template is `open` for ever and its runs
  are separate rows, so ticking off today's brief cannot destroy the schedule.
- `parent_chat` — set when the run belongs *in* a conversation (a timer, a
  deferral that asked to resume in place); null for an unattended run, which gets
  a fresh chat titled after the job.
- `resource`, `depends_on`, `deadline`, `priority`, `attempts`, `last_error`,
  `payload`, `meta` — as in `0009`.

## 3. Migration `0010_unify_tasks.py`

Three moves, all non-destructive, ids preserved on both sides (an automation
keeps its id as a job, so webhook URLs and bookmarked run links still resolve):

1. `automations` → `jobs`, `kind='task'`, `trigger='rrule'`, `executor=model_id`,
   `is_active` → `open`/`paused`.
2. `automation_runs` → `jobs`, `kind='run'`, `parent_job=automation_id`.
   `success` maps to `done` — that is what it meant, since the old runs were
   fire-and-forget. Runs created from here on land `needs_review`, which is a
   real behavioural change, not a re-labelling.
3. `jobs.parent_job` is added, plus `ix_jobs_parent_job`.

`automations` and `automation_runs` are left populated and kept in lockstep for
one release (`sync_legacy_template` / `sync_legacy_toggle` /
`sync_legacy_run_status` in `task_scheduler.py`, guarded by try/except and a
`logger.warning`), so a rollback lands on a working table. `downgrade()` drops
`parent_job` and says plainly that this loses the rows created after the
migration — `automations` itself was never touched.

## 4. One scheduler

`cptr/utils/task_scheduler.py` replaced two loops and a poll:

- `tick()` — promote due `rrule` templates into `trigger='at'` rows carrying the
  template's payload (`next_run_at` advanced from the `rrule`), then move due
  `at` rows `open → queued → running` and launch each as an ordinary chat turn.
  Starting a turn through the normal path is the point: tools, approval,
  streaming, usage — all identical to a chat the human started.
- `recover_tasks()` — at boot, rows left `running` by a crash become `failed`
  ("interrupted by restart"); a job whose chat was mid-turn is requeued rather
  than failed. Unattended runs land `needs_review` with the output read back from
  the run's own assistant message; `done` stays a human-only transition.
- `cancel_due_jobs_for_event()` — retires `at` rows whose `meta.cancel_on`
  matches an event (the old `cancel_on` semantics, now enforced on the job row).
- `run_task_now()` — the webhook / `POST /api/jobs/{id}/run` path.

`app.py`'s lifespan starts `recover_tasks` + `task_scheduler_loop` and says in a
comment that the old automations loop **must not** run again.

## 5. Timers: a boot fold, not a loop

A pre-0010 timer is a *dormant internal child chat* with `meta.timer_at`
(nanoseconds), `timer_model_id`, `parent_chat_id` and a prompt message. Nothing
polls those any more, so one caught mid-wait would never wake.
`fold_legacy_timers()` (`cptr/utils/timers.py`, called from the lifespan) moves
each pending one into the queue:

- reads `timer_at` / model / parent chat / prompt out of the child chat's meta
  and messages;
- creates `trigger='at'`, `trigger_at=timer_at`, `executor=timer_model_id`,
  `parent_chat=parent`, `source='chat'`, with the same `meta` markers the `timer`
  tool writes (`timer: true`, `cancel_on`, `origin_message_id`) — so the folded
  timer behaves as a late message to the parent chat, not a fresh unattended run;
- marks the child chat `status='folded'` + `folded_into_job=<job id>`, so a
  second boot does not duplicate the work;
- a chat caught mid-launch (current message is an unfinished assistant
  placeholder) has the placeholder deleted and is folded from its parent prompt —
  what `recover_timers` used to do;
- a row missing its time, chat or prompt settles `error`
  ("folded at boot: timer is missing its time, chat or prompt") rather than
  silently disappearing.

`timer()` itself now writes a job directly (`cptr/utils/tools.py`), so
`timers.py` is down to `parse_timer_at` (the `at` grammar, shared by `timer` and
`defer_workspace_todo`), `_set_timer_status`, and the fold. `timer_worker_loop`
and `recover_timers` are gone; `app.py` no longer starts them.

## 6. Timestamp units — the one bug this produced

Two units already existed in the tree, and unifying the storage made them
adjacent for the first time:

| column | unit |
|---|---|
| `jobs.created_at` / `updated_at` | **milliseconds** (`int(time.time() * 1000)`, as `chats`) |
| `jobs.trigger_at`, `next_run_at`, `meta.timer_at` | **nanoseconds** |
| `chats.created_at`, `chat_messages.created_at` | milliseconds |
| legacy `workspace_todos` | milliseconds |
| legacy `automations` / `automation_runs` | nanoseconds → `CAST(x / 1000000 AS INTEGER)` in `0010` |

`0010` converts `created_at` but passes `trigger_at` through untouched, because
both `automations.next_run_at` and `jobs.trigger_at` are ns; a job row mixing the
two units would sort decades out of place.

The Tasks view then fed `job.last_run.created_at` (ms) through `nsToMs()`, which
divides by 1e6 — `1.79e12` ms became `1.79e6` ms, i.e. **January 1970** — and the
runs tab dated every row `Jan 1, 01:29 AM`. Fixed by formatting it directly
(`formatWhen(job.last_run.created_at)` in `TaskList.svelte`), with the comment
"`created_at` is milliseconds (like `chats`); only `trigger_at` is ns" left where
the mistake was, and the probe asserts it: `epoch1970: false`,
`runsEpoch1970: false`, `runListEpoch1970: false` over the live page — a task row
reading `Sep 23, 06:00 PM · Weekly · in 21 hrs` and a run row `Sep 22, 03:30 AM`.

## 7. One page

- `GET/POST/PATCH/DELETE /api/jobs`, `/{id}/defer`, `/{id}/cancel`,
  `/{id}/run`, `/{id}/toggle`, `/{id}/runs`, `/{id}/review`, `/{id}/snooze`,
  `/meta/config`. There is deliberately no `/api/tasks`: the queue is the API,
  and `/api/todos` and `/api/automations` remain as *shims* over the same rows for
  the tool surface and for the webhook URLs.
- `TaskList.svelte` is the one list — `workspace` prop scopes it to one
  workspace (the dashboard's board), unset it is grouped by workspace (the Tasks
  tab at `/scheduled`, which is every task the user has: plain todos, reminders
  waiting for their moment, deferrals, recurring templates and their runs).
  `/automations` and `/automations/<id>` still exist as redirects to `/scheduled`,
  and `/scheduled/<id>` opens that row.
- The dashboard no longer calls the automations API at all: `loadBoard()` reads
  `/api/todos` + `/api/jobs` and the schedules are rows of the same list.
- i18n moved to a `tasks.*` namespace (62 keys × 10 locales); the last four
  `automations.model` references (Admin → Audio, Admin → Images) are now
  `tasks.model`. **The 55 `automations.*` keys are now unreferenced** — kept for
  one release alongside the shim, and removable when the legacy tables go.

## 8. Verification

- `pytest` full suite green (**94 passed**), including `tests/test_jobs.py`:
  rrule promotion, template/run split, pause/resume, the legacy mirrors, and the
  fold cases — a pending timer becomes the right job at the same instant, a
  second fold is a no-op, a timer interrupted mid-launch recovers.
- Live E2E on a copy of the real database (`CPTR_DATA_DIR=/tmp/unify-real`,
  port 4210, real cookie minted by the harness): `/scheduled` renders the whole
  task list grouped by workspace; no 1970 dates anywhere; the runs tab is correct.
- The fold was proven on the same lane by seeding a legacy timer chat due five
  minutes out: boot logged `Folded 1 legacy timer(s) into the job queue`, the
  chat became `folded` with `folded_into_job`, the job appeared as a row on
  `/scheduled`, and it then **fired by itself** — `running`, attempt 1, the
  timer's prompt written into the parent chat and the reply streaming back. The
  same run proves the row on the page and the run behind it are the same
  database row.

## 9. What is not done

- **Legacy tables and mirrors.** `workspace_todos`, `automations` and
  `automation_runs` are still written in lockstep for one release. Dropping them
  (and the three `sync_legacy_*` helpers, and the 55 dead `automations.*` locale
  keys) is the follow-up.
- **Folded timer chats.** They stay as `folded` rows in `chats` — harmless, but a
  cleanup for the same release that drops the legacy tables.
- **Phases 3-4 of the queue design** — pricing, `trigger='window'`, `resource`
  serialization, retry policy, worktrees. The `resource` and `rrule` columns are
  live; `deadline`, `priority`, `depends_on` exist and are unused by the UI.
