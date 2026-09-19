# NOTES — ui_metrics tool + dwell instrumentation

## Why

The `ui_events` table had grown to ~6.7k rows but answering "where does my time
actually go?" meant hand-writing SQL each time, and it was easy to get wrong:
`created_at` is *server receive* time (batches arrive late, so it scrambles the
real order), and legacy rows written before migration 0006 hold page-relative
`performance.now()` values in `ts`, not epoch-ms.

So the method — not the SQL — is the valuable part. It's now encoded twice: in a
read-only tool, and via direct measurement in the frontend.

## 1. `ui_metrics` tool (`cptr/utils/tools.py`)

Read-only. Registered in `TOOLS` with `approval: "allow"` and grouped under
`BUILTIN_TOOL_GROUPS["telemetry"]` so it can be toggled in Admin → Models.
It needs no workspace, so it is deliberately **not** in
`GLOBAL_CHAT_DISABLED_TOOLS`.

Reads the DB of the *current lane*, so numbers always match the instance you're
running (each lane has its own data dir).

| metric | what it returns |
| --- | --- |
| `dwell` | active time per workspace, with its share |
| `slow` | p50/p95/max per `(kind, label)`, slowest p95 first |
| `inventory` | sample counts by kind, session count, time span |

Params: `window_hours`, `cap_s` (dwell fallback only), `kind`, `limit`.

Backed by `UiEvent.scan(since_ms)` (`cptr/models/ui_events.py`), which orders and
filters on `ts` and **skips rows where `ts <= 1e12`** (those are the legacy
relative-clock rows that cannot be placed on a timeline).

Context params must be keyword-only (`*, __context__`) — otherwise the schema
generator exposes them to the LLM. Verified:
`_fn_to_schema("ui_metrics", ui_metrics)["parameters"]["properties"]` lists
only `metric, window_hours, cap_s, kind, limit`.

## 2. `dwell` measurement (`cptr/frontend/src/lib/utils/perf.ts`)

"Time spent in workspace X" is not derivable from interaction samples alone —
it needs the *gaps*, and a gap is indistinguishable from being away. So it is
measured directly: a span stays open while the document is **visible** in one
workspace, and closes on

- workspace change → `label: "switch"`
- document hidden → `label: "hidden"`
- unload → `label: "unload"`

Spans under 1s are dropped as flicker; a single span is clamped to 30 min so a
laptop sleeping mid-session doesn't log hours. `syncDwell()` is idempotent and
called from `record()`, so the span is closed *before* the event that caused the
switch is appended — which keeps attribution correct.

`ui_metrics(metric="dwell")` prefers these samples and says so; with none in the
window it falls back to the old gap-attribution estimate and prints the caveat.

## 3. Poller gate (`cptr/frontend/src/lib/components/chat/ChatPanel.svelte`)

`refreshCommandSessions` was polled every 5s by *every mounted chat tab*,
including hidden ones — the single biggest source of background traffic
(~180 req/min with 15 tabs open). Now driven by a `$effect` gated on
`active && docVisible && chatId`, plus a `visibilitychange` listener, so hidden
tabs and backgrounded browser windows poll nothing.

## Caveats

- Dwell counts only visible time; the in-flight span for the current workspace
  isn't flushed yet, so the newest workspace always looks slightly short.
- Everything here is single-user and low-volume so far — treat share-of-time as
  a ranking, and don't over-read small per-workspace differences.
- The `slow` metric is a plain aggregate; it does not yet correlate a slow
  interaction with `total_tabs` (that data is in `meta`).
