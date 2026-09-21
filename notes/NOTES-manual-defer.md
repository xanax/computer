# Manual defer: "I'll do it myself"

The run-later form (`WorkspaceDashboard.svelte`) has a checkbox next to the model
dropdown. Ticking it disables the dropdown and schedules the row **for the user**
instead of for a model.

## The contract

`executor: "human"` — the same executor a plain todo has. Nothing new is needed
in the backend; three existing rules make it work:

* `routers/jobs.py::defer_job` stores whatever executor the body carries, so the
  form just sends `"human"` instead of a model id.
* `utils/jobs.py` (worker) early-returns for `EXECUTOR_HUMAN`, so a due row is
  never claimed or run.
* `models/jobs.py::mark_due_queued` filters `Job.executor != EXECUTOR_HUMAN`, so a
  due manual row stays `open` rather than flipping to `queued` — it keeps reading
  as the user's own, not as a run starting.

Submit rules: `at` is required; the executor is required **unless** manual mode is
on, in which case an empty `deferModel` is fine (the dropdown may have no models
at all).

## UI

* `.defer-manual` label + `input[type=checkbox]` bound to `deferManual`.
* `.defer-model:disabled` draws the dead dropdown from the theme (`--app-fg-muted`
  text, `--app-divider` border, `opacity: 1`) instead of the browser's greys — in
  `bw`/`bw-dark`, `--app-fg-muted` is pure ink (`appearance.ts:288`), so it stays
  ink-on-paper.
* `deferManualHint` = "Nothing runs — the row waits for you."
* A scheduled manual row shows `reminderWaiting` ("waiting for you · <time>"),
  and `reminderDue` once its moment passes.
* Locales: `dashboard.deferManual` / `deferManualHint` / `reminderWaiting` /
  `reminderDue` in all 10 locales via `scripts/add-manual-defer-locales.py`
  (`--check`).

## Verifying it

`.cptr/harness/probe-defer-manual.js` adds a todo, ticks the box, schedules it for
15 minutes, reads the row back and removes it again.

```
LD_LIBRARY_PATH=~/.cache/ms-playwright/host-libs node .cptr/harness/cdp.mjs \
  --url "http://127.0.0.1:4200/?view=dashboard&workspace=/home/brendan/computer" \
  --js .cptr/harness/probe-defer-manual.js --wait 7000
```

Two traps, both learned the hard way:

1. The dashboard is `/?view=dashboard&workspace=<path>`. A bare `/` renders the
   home layout with no board at all — the probe then fails with "no clock button".
2. **The board is rarely empty.** The probe's clock click goes to the *first* row,
   which is the user's. Scoping the probe to `probe:`-titled rows and undoing the
   mutation (executor/trigger/trigger_at/meta, via `Job.update_by_id` plus a
   `PATCH /api/jobs/{id}` to broadcast the change to an open board) is what keeps
   this safe to run.

Last verified run: checkbox label "I'll do it myself", select `disabled` false → true
with 22 models on offer, hint present, `sent = {at, executor: "human", payload}`,
reply `{executor: "human", trigger: "at", status: "open"}`, row detail
"waiting for you · Sep 21, 09:33 AM · in 14 mins".
