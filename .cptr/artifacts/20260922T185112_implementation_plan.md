# Unify tasks: one store, two views

## The shape you asked for

solcase has exactly two surfaces and they share one row type:

| solcase | cptr today | cptr after |
|---|---|---|
| Overall tab → every case's schedule | `/scheduled` → **automations only**, no manual or deferred tasks | `/scheduled` → **every task in every workspace**, grouped by workspace |
| Each case → its own schedule | Workspace dashboard → todos list **and** a separate "upcoming" automations list | Workspace dashboard → **the same task list**, scoped to that workspace |

Cases ≈ workspaces (a directory), so this maps 1:1. The important part is that the *row* is the same in both views — only the scope filter differs.

## What exists today (four stores, one row type)

You have already built the right table. `jobs` (migration 0009) was explicitly designed as "human todos, one-shot defers and recurring automations are the same row seen through different columns":

```
jobs(workspace, resource, title, kind, executor, trigger, trigger_at, rrule,
     deadline, payload, status, priority, depends_on, attempts, last_error,
     parent_chat, source, origin_chat, meta, created_at, updated_at)
```

Live counts in `/home/brendan/.cptr/app.db`:

| store | rows | notes |
|---|---|---|
| `jobs` | 8 (7 human, 1 `trigger='at'` → `deepseek-v4-pro` in brogue-js-llm) | the spine; `workspace_todos` is already a shim over it |
| `workspace_todos` | 7 | what the dashboard's todo list actually reads |
| `automations` | 10 (7 active, 4 workspaces) | what `/scheduled` reads; `rrule` + `next_run_at` |
| `automation_runs` | — | run history, one row per firing |
| chat `meta.timer_at` | 0 right now | in-chat timers; **visible in no list anywhere** |

So the remaining work is not "invent a task store" — it is **retire the three stragglers into `jobs`** and put one UI on top.

## The model

One row, four axes. Nothing new is invented:

| axis | values | meaning |
|---|---|---|
| `kind` | `task` / `note` / `run` (new) | actionable / not actionable / an occurrence of a recurring task |
| `executor` | `human` / `<model_id>` | you do it / an agent does it |
| `trigger` | `manual` / `at` / `rrule` | no time / one moment / repeats |
| `status` | `open, queued, running, blocked` → `done, needs_review, failed, cancelled` | one machine for all of it |

Your three answers become: `trigger='manual'` + `executor='human'` = today's todo. `trigger='at'` + model = today's defer. `trigger='rrule'` = today's automation, and it ends `needs_review` like any other run.

### The one design call I'm making for you (flag if you disagree)

**Recurring tasks get template rows + run rows.** The `rrule` task stays `open` forever with `trigger_at` = next occurrence; each firing creates a child row (`kind='run'`, `parent_job=<template>`) that walks `running → needs_review → done`.

Without this, one row has to be a schedule *and* a result, and "mark the 09:00 brief as read" destroys the schedule. With it, `automation_runs` has a drop-in replacement and "all tasks" can show scheduled-but-not-yet-run and ran-and-waiting-for-you as different things.

## Two views, one component

- `lib/components/tasks/TaskList.svelte` + `TaskRow.svelte` — the only list in the codebase.
  - props: `workspace?: string` (omitted = all), `groupByWorkspace`, `showRuns`, `emptyHint`.
  - keeps the `--app-bg/fg/fg-muted/border/divider/hover` tokens so bw/bw-dark stay ink-on-paper with solid inversions for hover/active (no grey washes).
- `/scheduled` (label changes to **Tasks**; keep the URL so existing links keep working) — all workspaces, grouped by workspace, filter chips: *All · Mine · Agent · Recurring · Needs review*. This is the solcase overall tab.
- `WorkspaceDashboard.svelte` — embeds the same component with `workspace={workspace}` and **deletes** its two separate sections (the `todos`/`pendingRequests` list and the `upcoming` automations list). This is the solcase per-case schedule.

## Phases

| # | Deliverable | Risk |
|---|---|---|
| **T1** | migration `0010_unify_tasks.py`: add `parent_job` column + index; allow `kind='run'`; backfill `automations` → `jobs` (`trigger='rrule'`, `executor=model_id`, `payload=prompt`, `meta.webhook_token`, `next_run_at`); backfill `automation_runs` → `kind='run'` rows. **Nothing dropped**; `automations`/`automation_runs` kept, dual-written for one release. | low |
| **T2** | one scheduler: `cptr/utils/task_scheduler.py`. Per poll: promote due templates → spawn run rows; claim due `trigger='at'`; watch runs. Port `execute_automation`'s fresh-chat path into `run_job`. Retire `scheduler_worker_loop` from `utils/automations.py`; `utils/timers.py` keeps its loop until T5. | medium |
| **T3** | one read API: `/api/tasks` (alias `/api/jobs`), `workspace` optional so omitting it means *all workspaces*. Add `/<id>/runs`, `/<id>/approve` (`needs_review → done`), `/<id>/snooze`. `/api/todos` and `/api/automations` become thin shims (todos already is). | low |
| **T4** | the two views above; nav label; `automations.title`/`dashboard.todos`/`dashboard.upcoming` keys replaced by one `tasks.*` group via `scripts/add-*-locales.py` across all 10 locales. | low |
| **T5** | timers fold in: the `timer` tool writes a `jobs` row (`trigger='at'`, `parent_chat`, `cancel_on` in `meta`); the job scheduler learns `cancel_on`; then retire `timer_worker_loop` + `recover_timers`. Own tests. | **high** — `_BUSY` requeue, `get_pending_input_lock`, boot recovery |
| **T6** | the two things the original design deferred and never built: `resource` lanes (serialize per repo) and `deadline` + `trigger='window'` pricing. Out of scope unless you want them. | high |

Cross-workspace live updates need no backend work: `emit_jobs_changed` already carries `workspace`, so the global page just reloads on any `jobs_changed` it sees.

## What you're missing

1. **`resource` lanes were never built.** The column exists; nothing enforces it. Three tasks scheduled for 09:00 in one repo = three agents writing one tree. The house rule "space your agent tasks apart" is currently the only guard. Highest-value T6 item.
2. **Missed occurrences collapse, never backfill.** `claim_due` sets `next_run_at = next_run_ns(...)` computed *at claim time*, so 5 days of downtime on a daily task yields **one** catch-up run, not five. (Your nightly backup depends on this behaviour — don't "fix" it globally.)
3. **Everything ending `needs_review` is a permanent backlog.** You declined the report-only flag, so a daily brief leaves one review item per day, forever. Mitigation to consider: bulk "dismiss all reports", or revisit `report_only` once the volume hurts.
4. **`depends_on`/`blocked` are dead columns** — nothing ever sets them. If you want "B after A", now is the cheap moment; otherwise drop them and stop pretending.
5. **"This occurrence / all occurrences"** for editing or snoozing a recurring task is a UI decision nobody has made. Needed the moment you can edit a recurring row.
6. **A task belongs to exactly one workspace, and the workspace can vanish.** `/home/brendan` (home) is already used as a pseudo-workspace to hold a task. The global view will show tasks for directories that no longer exist.
7. **Nothing tells you a run landed.** `needs_review` happens silently; `notify()` has no default target configured in your env. Without this, "all tasks" is only visible when you go looking.
8. **`todo_requests` stays separate on purpose** (approval ≠ storage), so the board can show two kinds of "waiting on you": a proposed todo change, and a run's result.
9. **Webhooks are automation-only.** `meta.webhook_token` + the public trigger URL must be carried through T1 or the feature disappears silently.
10. **`priority` and `deadline` are set by nothing** and `list_for_workspace` orders oldest-first, so priority currently only affects the scheduler's claim order.

## Limitations (what this will not fix)

- **Timezone.** RRULEs carry no timezone: `_parse_rule` uses `ignoretz=True` against the server's local `datetime.now()`, and `ScheduleDropdown` writes the raw local hour (`BYHOUR=9`). Correct only while the server's clock matches yours; a TZ move or a DST boundary shifts things silently. Fix = a `tz` column or UTC-normalised rules, which *changes existing rows'* behaviour.
- **SQLite, two servers.** `Job.claim_due` is a single conditional `UPDATE ... RETURNING` and is genuinely safe. `Automation.claim_due` is select-mutate-commit and is **not** — and you have had two cptr servers up at once (4210 and 4319). Unifying onto `jobs` fixes this incidentally; until T2 lands, coexist carefully.
- **Timers stay chat-backed until T5**, and their poll is a full `chats` scan filtered in Python (no index), O(chats) every `TIMER_POLL_INTERVAL`.
- **During T1–T3 there are two loops touching the same rows.** `trigger='at'` must be owned by exactly one of them or `_BUSY` requeues/runs race.
- **"Done" is ambiguous for a template.** Templates are never done — only runs. The UI must make that obvious or you'll try to tick off a schedule.
- **Rollback.** The migration is additive, but once automations start firing as job rows, a code rollback double-runs or skips occurrences unless `automations.next_run_at` is kept in lockstep through T1–T3 (I'll do that).
- **i18n is uneven.** Only `en.json` has all 58 `dashboard.*` keys; the other nine have 18. New `tasks.*` keys go through the locale script, or nine languages silently show English.
- **`automation_runs` kept for a release = two history stores.** `AutomationsPanel`'s run list must read the new rows or it goes stale the moment T2 lands.
- **This is a UI/plumbing unification, not a capability.** It does not add lanes, pricing windows, retries, or review automation; it makes what you already have legible in one place, which is the actual complaint.

## Sequencing recommendation

T3 + T4 first (read-only + UI) would give you the solcase shape in a day, on top of the stores as they are — but with two status vocabularies and run history still split, which is exactly what you said you didn't want. Do **T1 → T2 → T3 → T4** as one change, then **T5** on its own with tests, then decide on **T6**.
