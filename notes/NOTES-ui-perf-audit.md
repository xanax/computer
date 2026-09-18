# UI Performance / Monitoring Audit (for speeding up tabs etc.)

Date: current

## Summary
- **No built-in monitoring or telemetry today.** Zero `performance.now()`, `PerformanceObserver`, timings, or event logging for UI interactions.
- **Tab system is "eagerly mounted, CSS-hidden"** (not lazy).
- **Lots of heavy things stay alive** when you have many tabs/groups open.
- **SQLite is perfectly usable** for storing this (we already use it heavily via SQLAlchemy + Alembic). We can add a table or just shove JSON under `user_states`.

## Current Tab / Rendering Architecture

From `+page.svelte` + `GroupTabBar.svelte` + styles:

```svelte
{#each group.tabs.filter((tab) => tab.type === 'terminal' ...) as tab}
  <div class="persisted-tab" class:persisted-tab-hidden={tab.id !== activeTabId}>
    <Terminal ... />
  </div>
{/each}
<!-- same for chat, file, browser, files -->
```

CSS (end of +page.svelte):
```css
.persisted-tab { position:absolute; inset:0; ... }
.persisted-tab-hidden {
  visibility: hidden;
  z-index: 0;
  pointer-events: none;
}
```

**Implications:**
- Switching tabs = basically free (just change a store value + CSS classes).
- But keeping N terminals + N CodeMirror editors + N full ChatPanels (with streaming listeners, TTS prep, IntersectionObservers, etc.) + browser iframes alive costs memory + CPU + battery.
- Terminals always have active WebSocket + xterm + fit logic + wakeLock.
- Chats have windowing (good: only last ~12 messages rendered) + IntersectionObserver for older history, but still keep full `allMessages`, socket listeners, command session polling, TTS queue etc. for inactive chats.
- FileEditor = full CodeMirror instance per open file tab.
- BrowserPreview keeps iframe or ChromeBrowser session.

`active` prop is passed to ChatPanel and BrowserPreview but has **very light usage** (mostly cosmetic or passed through). Terminals ignore it completely.

## Lazy Loading / Deferral Today

- Chat windowing + IntersectionObserver for history (good).
- Terminals / browsers are only created when the tab is first opened (not pre-created).
- Welcome page does some resume prefetching.
- No dynamic imports / code splitting for the big tab bodies.
- No virtual scrolling outside chats.
- No suspend / {#await} for heavy tab content.
- No tab "sleep" or "discard" when not visible for a while.

## What "monitoring information" we currently have

**Frontend:**
- None. No timings on:
  - Tab click → visible paint
  - Component mount times (xterm init, codemirror, chat load)
  - Number of open tabs / groups
  - Memory pressure signals
  - Long tasks
- We have some derived stores (`activeTab`, `activeGroup`) and `tabHistory` (MRU) which could be leveraged.

**Backend / existing data:**
- Workspace state (tabs + groups + layout) is persisted per workspace in the `workspaces` table (JSON blob).
- Global prefs in `user_states` (JSON).
- Chats have timestamps, but nothing UI-performance related.
- No `ui_events`, `perf_samples`, etc.

**DB is ready:**
- WAL mode, aiosqlite, Alembic already wired.
- Adding a table is low friction (new migration + model).

## Where the real cost probably lives (educated guess from code)

1. Multiple live xterm + PTY WS connections.
2. Multiple full CodeMirror instances (especially with git diff, syntax, large files).
3. ChatPanels keeping socket listeners + command session polling + TTS prep maps even when hidden.
4. Browser iframes / chrome sessions.
5. Accumulated DOM from long chats (mitigated by windowing) + many open groups/splits.
6. No debouncing / coalescing on some resize/fit/refresh paths.
7. Store updates on every keystroke / output in terminals/chats can cause broader reactivity.

Clicking tabs themselves is probably already fast; the pain comes from "having lots open" (background cost) + first open of a heavy tab type.

## Storing in SQLite — yes we can (and should)

Options (in order of recommendation):

A. Lightweight dedicated table (best for querying later):
   - `ui_perf_events(id, created_at, workspace_path, event, duration_ms, meta JSON, tab_type, open_tab_count, ...)`
   - Or a sampled rollup table.

B. Just stuff under `user_states` JSON (fastest to ship, no migration):
   - e.g. `user_states.data.ui_perf = { last_tab_switches: [...], aggregates: {...} }`

C. Hybrid: collect in memory, flush batches to a new table on idle or on close.

We already have `savePreferences` / workspace save paths that go through the state router → models.

Adding a small collector:
- A tiny Svelte store or class that does `performance.mark` + `performance.measure`.
- Hook `setActiveTab` + component `onMount` / first meaningful paint for the 4 heavy types.
- Periodically or on visibilitychange flush a few samples.
- Backend endpoint (or reuse `/api/state/preferences` with a special key) or a tiny new router.

We could also expose a dev-only "UI Perf" panel later that reads it back.

## Recommended first instrumentation (minimal)

1. Wrap tab activation:
   - Time from `setActiveTab` call → next paint (or `requestAnimationFrame` + `performance.now`).

2. On mount of heavy components, record:
   - Terminal: time to xterm + first WS attach.
   - FileEditor: time to CodeMirror ready.
   - ChatPanel: time to first messages rendered (after load).
   - Browser: time to iframe/chrome ready.

3. Snapshot on tab switch + periodically:
   - Number of open tabs total + by type.
   - Number of groups/splits.
   - Rough "is visible" state.

4. Store samples (batched, low volume) into SQLite.

5. Optional: listen for `longtask` or use `PerformanceObserver` for paint/measure.

## Status: implemented

### Storage — `ui_events` table (migration `0005`)

Append-only rows, one per sample: `id`, `user_id`, `workspace`, `session_id`,
`kind`, `label`, `ts` (client `performance.now()`), `duration_ms`, `meta` (JSON),
`created_at` (server epoch-ms). Indexes on `(kind, created_at)` and
`(label, created_at)`.

- `cptr/models/ui_events.py` — model + `bulk_create` / `recent` / `summary` /
  `prune` / `clear`.
- `cptr/migrations/versions/0005_add_ui_events.py`.
- `cptr/routers/perf.py` — `POST /api/ui-events` (batch ingest, capped at 200
  events), `GET /api/ui-events/summary`, `GET /api/ui-events/recent`,
  `POST /api/ui-events/prune`, `DELETE /api/ui-events`.

`summary` returns `count`/`p50`/`p95`/`max`/`mean` per `(kind, label)` — or per
kind alone when called with a `kind` filter — sorted slowest p95 first, which is
the order worth fixing things in.

### Collector — `cptr/frontend/src/lib/utils/perf.ts`

In-memory buffer → flush every 5 s or at 40 samples → `POST`. Final flush on
`pagehide`/`visibilitychange` via `sendBeacon` so samples survive a tab close.
Best-effort throughout: failures are swallowed and the buffer is capped, so a
down backend can never slow down or crash the UI. Disable with
`localStorage['cptr.perf.disabled'] = '1'`.

API: `record`, `trace`, `traceAsync`, `measureToPaint`, `markSince`,
`perfMount` (Svelte action), plus a `PerformanceObserver` on `longtask`
(Chromium-only; silently skipped elsewhere).

### Instrumented paths

| kind | where | what it captures |
| --- | --- | --- |
| `tab_switch` | `stores.setActiveTab` | state change → paint, with `total_tabs` / `groups` / `tabs_in_group` |
| `mount` | `use:perfMount={tab.type}` on every `.persisted-tab` (9 sites) | mount → paint per tab type |
| `dir_list` | `DirectoryPicker.fetchDirectories` | full listDir round-trip + entry count, `network` vs `error` |
| `dir_navigate` | same | `cache_fresh` vs `cache_stale` hits (no network) |
| `dir_paint` | same | list arrival → paint |
| `dir_prefetch` | `DirectoryPicker.prefetch` | hover-warmed cache fills |
| `long_task` | `PerformanceObserver` | main-thread stalls ≥50 ms with start offset |

Samples carry the active workspace path (`setPerfContext` in `stores.ts`), so a
`/mnt/c` workspace can be told apart from a native one.

### Reading the data

```bash
curl -s -b "cptr_session=$TOKEN" localhost:4200/api/ui-events/summary | jq
curl -s -b "cptr_session=$TOKEN" 'localhost:4200/api/ui-events/summary?kind=tab_switch' | jq
curl -s -b "cptr_session=$TOKEN" 'localhost:4200/api/ui-events/recent?limit=20' | jq
```

`meta.total_tabs` vs `p95` on `tab_switch` is the lazy-loading question: if p95
climbs with tab count, tabs need to mount on first activation rather than all at
once.

### Still to do

- A settings panel to read this back in-app (needs care for the bw/bw-dark
  e-ink themes: solid inversion, no grey washes).
- Retention: `POST /api/ui-events/prune` is manual today; call it on startup or
  from an automation to keep the table small.

## The WSL cost model behind all of this (measured)

`/mnt/c` goes through the 9p bridge. Measured on this machine:

| operation | ext4 | 9p (/mnt/c) |
| --- | --- | --- |
| readdir entry, already-open dir | ~1us | ~7us |
| open + readdir + close a directory | ~0.04ms | **~5ms** |
| `stat(2)` one path | ~0.005ms | **~1-5ms** |

Two consequences worth remembering:

1. **Directories, not entries, are the expensive unit.** Listing a 116-entry
   folder is fine, but *descending* into subdirectories costs ~5ms each, so the
   cost driver is the number of directories visited.
2. **d_type is provided by 9p**, so `os.DirEntry.is_dir()/is_file()` are free.
   Only size/mtime need a `stat`. A walk that classifies entries from the
   dirent is 2-4x faster than one that stats each entry.

`_list_directory` was fixed first (scandir + `dirs_only` + TTL cache). The same
reasoning applied to the agent's file-tree tool:

### `runtime._list_tree` (agent `list_directory` tool)

The old non-recursive branch cost was `sum(1 for child in item.rglob("*") if
child.is_file())` — one `stat(2)` per file just to decide whether it *was* a
file, run for every subdirectory of the listing, and descending into
`.git`/`node_modules` that the listing itself hides. On `/mnt/c` that is
minutes, and it silently inflated every count.

Now: `os.scandir` + dirent classification everywhere, `_TREE_IGNORE` pruned
*inside* the count walk (counts now match what is displayed), symlinked
directories are never descended into (no link cycles), and all of it runs under
a shared `_ScanBudget` — ~1s wall clock plus ceilings on dirs/files/stats/lines.
Anything not reached is marked `?` and any count that hit a ceiling is marked
`+`, with a footer line explaining the markers, so an expensive tree degrades
instead of hanging.

| target | old | new |
| --- | --- | --- |
| `/home/brendan/computer` (non-recursive) | 269ms | 1.1ms |
| `/home/brendan/computer/cptr` (non-recursive) | 861ms | 3.1ms |
| `/mnt/c/Users/brend/scripts` (non-recursive) | 258ms | 78ms |
| `/mnt/c/Users/brend` (non-recursive) | >60s | 1.0s (bounded) |
| `/mnt/c/Users/brend/AppData` (non-recursive) | >60s | 1.0s (bounded) |
| `/home/brendan` (recursive) | 4.7s / 46143 lines | 164ms / 2010 lines (capped) |

The deadline (not a fixed directory count) is what makes this work on both
filesystems: on ext4 the whole tree is still walked exactly, because 5000
directories only cost ~200ms there.

## Data quality pass (schema 0006)

First real look at the collected data (766 rows over 2.3h, 5 sessions) showed the
pipe worked end to end but the *content* was thin in four ways. Fixed:

**1. `ts` wasn't a clock.** The collector wrote `performance.now()` into `ts` —
page-relative, so it reset on every reload (rows read `ts: 1609.8`, `ts:
1108621.2`) and couldn't order events across sessions. The only usable clock was
`created_at` (server, stamped *per batch*, so a whole 5s flush shared one
timestamp). Now:

- `ts` = `Date.now()`, a real epoch-ms timeline that survives reloads.
- `perf_ms` (new column, migration `0006`) keeps `performance.now()` for precise
  intra-page gaps that are immune to clock adjustments.

Legacy rows keep page-relative `ts` and null `perf_ms`; don't mix them.

**2. Ambient context on every sample.** `mount` carried no metadata at all (137
rows) while `tab_switch` carried tab counts (16 rows) — the context lived in the
one place the volume wasn't. The store now registers a `setPerfContext` provider
and `record()` merges it into every event's `meta`:

`total_tabs`, `groups`, `active_tab`, `hidden`, `focused`, `viewport`

captured **at record time**, not flush time — which also fixes the 52 rows that
shipped with `workspace: null` (they'd resolved context 5s later, or on unload,
when the store had moved on). `workspace` is now per-event and the batch value is
only a fallback.

**3. Long tasks were 75% of the table and blamed nothing.** All 574 were
`label: "self"` with just `{start}`. `record()` now remembers the last real
interaction and the observer attaches `during` + `since_ms` when a long task
begins within 1.5s of it, so a block can be attributed to the switch/nav that
caused it. Sub-80ms entries are dropped as noise.

**4. rAF measurement was silently corrupted by background tabs.** A hidden tab
has its `requestAnimationFrame` callbacks throttled (or paused), so every
`measureToPaint` event inflates. Samples now carry `started_hidden`, and
`mount` carries `active` (was this tab the visible one?), so throttled readings
can be discounted instead of read as slowness.

Also: cache-served `dir_navigate` samples are flagged `cache: true` so a 0ms
"instant hit" isn't averaged into navigation latency.

`perfMount` changed shape: `use:perfMount={{ label: tab.type, active: tab.id === group.activeTabId }}`.
