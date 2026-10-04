# A paused task must not advertise fire times

## Symptom
"Nightly cptr data backup" was paused (`is_active: false`) but the automations
list still returned five upcoming `next_runs`, and `next_run_at` still carried a
timestamp. The two paused IMSDB ingest tasks showed the same.

## It was never a scheduling bug
The obvious read — "a paused task still has next_runs, so it will fire after a
redeploy" — is **wrong**. `Job.promote_due_templates` (`cptr/models/jobs.py`)
requires `Job.status == STATUS_OPEN` in *both* the SELECT and the conditional
UPDATE that advances `trigger_at`, so a paused row is never promoted no matter
what `trigger_at` says. Verified in the live DB: the paused backup's
`trigger_at` is 2026-09-25, i.e. already in the past, and it has not fired.

## The real bug was cosmetic but load-bearing
`next_n_runs_ns(rrule, 5)` reads the rrule and **nothing else** — it does not
consult status. `trigger_at` likewise survives a pause. So both fields kept
being computed for rows that will never run, and the dashboard rendered them as
real fire times. That is the kind of wrong-but-plausible UI that makes a
scheduler look broken and sends you debugging the wrong layer.

## Fix
Report `next_run_at: None` and `next_runs: None` when `status != STATUS_OPEN`.
One place in the router (`_automation_dict`, covering all five call sites) plus
`list_automations`/`update_automation` in `cptr/utils/tools.py`. An open task
still previews its upcoming runs — that behaviour is deliberately preserved and
asserted in the tests, because "hide everything" would have been the easy wrong
fix.

`update_automation` re-reads the job after the UPDATE rather than trusting the
stale in-memory row, so editing a paused task's schedule cannot resurrect a
preview.

## Test
`tests/test_automation_next_runs.py`. Confirmed by reverting the fix: 2 of the 3
tests fail, and all 3 pass with it.

## Needs a server restart
The router and tools changes do not take effect until the cptr server restarts.
Until then the live API still returns `next_runs` for paused rows.
