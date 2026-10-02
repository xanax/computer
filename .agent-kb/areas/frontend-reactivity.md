---
area: frontend-reactivity
title: Store notifies, pane re-renders, and swallowed clicks
aliases: [stores, setActiveGroup, dashboard-click, rerender]
updated: 2026-10-02
---

# Store notifies, pane re-renders, and swallowed clicks

## Map
- `cptr/frontend/src/lib/stores.ts` — every client store; `currentWorkspace` (~line 407), the `updateIfChanged()` helper, and `subscribeForPersistence` (re-POSTs state on any change).
- `cptr/frontend/src/lib/components/WorkspaceDashboard.svelte` — workspace "dash" tab: prompt, Processes (`WorkspaceServices.svelte`), todo/job board, recent chats. The board lives behind `{#if loading}`.
- `cptr/frontend/src/routes/+page.svelte` — the pane host: `.split-pane` writes `setActiveGroup` in `onpointerdown` **and** `onfocusin` (:1425), `setHomeActiveGroup` for the home pane (:1021). Tab bar: `GroupTabBar.svelte`.
- `notes/NOTES-click-lost-to-pane-remount.md` — the full write-up, measurements, and the probe list; `BUGS.md` B-017.

## Facts
- [verified 2026-10-02 cptr/frontend/src/lib/stores.ts] A Svelte writable notifies on **every** `set`, and `update()` always calls `set` — an updater returning the unchanged object still runs every subscriber and still re-fires `subscribeForPersistence` (a real `PUT /api/state/workspace`).
- [ran 2026-10-02 cdp harness press on a pane] Before B-017 a single pointer press cost 4 board re-reads (`…/jobs`, `…/todos`) + 5 state `PUT`s, and removed/re-added `SECTION.services` in `.dashboard-body`.
- [ran 2026-10-02 cdp real clicks] After the fix: 0 board reads / 0 state saves; "Add service" opens its form; a services row Start/Stop really starts/stops the service (verified against `GET /api/state/workspace/services`).
- [verified 2026-10-02 cptr/frontend/src/routes/+page.svelte] A pane pointer press does **not** cancel the `click` that follows it unless the pressed node is detached in between — so a dead button usually means "the DOM was rebuilt mid-press", not "the handler is missing".
- [verified 2026-10-02 cptr/frontend/src/lib/components/GroupTabBar.svelte:406] A tab is a `<button>` and its close (x) is a `<span role="button">` **inside** it; a tab with no close span is `permanent`. Chat tabs are permanent.
- [ran 2026-10-02] Declared services are not a DB table: `~/.cptr/app.db` has none, and `workspaces.data['services']` holds them as JSON via `cptr/utils/services.py`. `~/.cptr/cptr.db` exists but is 0 bytes.

## Built
- `updateIfChanged(store, updater)` in `stores.ts` — notify only when the result actually differs; `setActiveGroup` uses it.
- `WorkspaceDashboard.svelte` keys `loading` on the workspace **path**: first paint for a workspace shows the loading state, a re-read of the same path only refreshes rows. Entry point to extend: the `loadBoard`/effect pair there.

## Decisions
- Fix the notify at the store, not by `stopPropagation`/`preventDefault` on the pane handlers — the pane handlers are how the active group is tracked, and every caller passes an id, so none of them depend on the notify-with-unchanged-value behaviour.
- Guard the *general* shape (`updateIfChanged`) rather than hand-patching `setActiveGroup` — `setActiveTab`, `setFileBrowserCwd`, etc. spread new objects unconditionally and are the same trap; convert them as they are touched. Rejected: removing the persist subscription's per-change write (that is what makes split/pane layout durable).

## Dead ends
- Chasing Svelte event delegation: a "recent chats" row *did* click fine, so delegation was never broken — that clue is what pointed at a re-render race instead. Do not re-open the delegation theory without a click trace showing no `click` event at all.
- Blaming the services section for not listening: the section is rebuilt, it is not dead. Reproduce by checking `document.querySelectorAll('.services').length` before and after the press.

## Do not redo
- Probing `~/.cptr/cptr.db` for services: it is empty; the live DB is `~/.cptr/app.db` and services are JSON in `workspaces.data`.
- Re-deriving the upstream split: `setActiveGroup`'s updater is byte-identical upstream @ `f9d1d8c` (probe `B-017` in `scripts/fork-probes.tsv`), but `WorkspaceDashboard.svelte` does not exist upstream at all, so the visible bug is fork-only.

## Open
- `setActiveTab` / `setFileBrowserCwd` / the other always-spread updates still notify on no-ops (harmless today; they are the same class of trap).
- The dashboard still re-reads todos/jobs on any `workspace` change; the re-reads are now invisible, but they are not deduplicated.
