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

## Next steps if we want to proceed

- Add a migration + simple `UiPerfEvent` model (or just use JSON for v1).
- Add a small `perf.ts` / `uiMetrics.svelte.ts` collector in frontend.
- Instrument the key paths (stores + the 4 big components).
- Wire a flush path (can be fire-and-forget to an existing endpoint or new lightweight one).
- (Later) a way to view/aggregate the data, or use it to auto-suggest "you have 12 terminals open, consider closing".

We have almost none of the *data* today, but we have a very clean place to put it (SQLite) and a tab system whose costs are easy to understand once we start measuring.

Want me to sketch the table + collector + a couple of instrumentation points?
