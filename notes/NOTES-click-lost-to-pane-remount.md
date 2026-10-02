# Clicks on the workspace dashboard did nothing ("it flashes then nothing happens")

BUGS.md **B-017**. Fixed 2026-10-02.

## Symptom

The dashboard rendered, then the first click on it did nothing: "Add service" never
opened a form, and any control in a pane could look dead. Not every click -- a
"recent chats" row sometimes worked -- which is what made it look random.

## Cause

Two halves, each harmless alone.

1. **Every pointer press in a pane wrote a no-op to the workspace store.**
   `+page.svelte` calls `setActiveGroup(group.id)` on `onpointerdown` *and* `onfocusin`
   of `.split-pane` (also `setHomeActiveGroup` for the home pane). `stores.ts` did:

   ```ts
   currentWorkspace.update((ws) =>
       ws && ws.activeGroupId !== groupId ? { ...ws, activeGroupId: groupId } : ws
   );
   ```

   Returning the same object does **not** stop the notify: a Svelte writable notifies
   whenever `set` is called, and `update` always calls `set`. So one pointer press =
   one or two subscriber runs (pointerdown + focusin), plus
   `subscribeForPersistence` re-POSTing `PUT /api/state/workspace`.

2. **The fork's dashboard blanked its whole body on every re-read.**
   `WorkspaceDashboard.svelte` had `loading = $state(true)` and an effect that set
   `loading = true` on *every* `workspace` change, clearing it only after
   `loadTodos`/`loadJobs` resolved. `{#if loading}` wrapped the entire board, so each
   notify destroyed and recreated the DOM between `pointerdown` and `pointerup`. The
   element the browser had pressed no longer existed, so no `click` was dispatched to
   it -- and `.services` is a section of that board, which is why the services buttons
   were the visible casualty.

Measured before the fix, on a real CDP `Input.dispatchMouseEvent` press inside the
pane: 4 board re-reads (`/api/state/workspace/jobs`, `.../todos`) and 5
`PUT /api/state/workspace` calls per single click, `SECTION.services` removed and
re-added in `.dashboard-body`.

## Fix

- `stores.ts`: new `updateIfChanged(store, updater)`; `setActiveGroup` (and the same
  shape elsewhere) use it, so an update that changes nothing does not notify. This also
  stops the pointless state POST.
- `WorkspaceDashboard.svelte`: the board stays mounted once the workspace path is
  known. `loading` is still shown for the first paint and when the path really changes,
  but a re-read of the *same* workspace only refreshes rows.

## Verified (after)

Real pointer presses via `.cptr/harness/cdp.mjs`:

| probe | before | after |
| --- | --- | --- |
| pane press (any) | 4 board reads, 5 state saves | 0 / 0 |
| `.services button.link` ("Add service") | nothing | form open, board mutated +2 nodes |
| dashboard tab in the tab bar | -- | `?view=dashboard`, dashboard mounts |
| recent-chat `button.row` | sometimes | chat opens |
| services row "Start" / "Stop" (tesla `kbd-bridge`) | -- | service actually starts / stops, pill follows |
| home screen "Continue" | -- | opens `?workspace=...` |

Evidence shot: `notes/kb-evidence/dash-services-form-open.png`.

## Traps for the next agent

- A no-op `update()` is not a no-op for subscribers. If you add a store write to a
  pointer/focus handler, it re-renders the pane on every press.
- Any `{#if}` that wraps a whole panel and is driven by "we are loading this thing"
  will eat clicks in that panel. Scope loading flags to the rows, or key them on the
  identity (path) of what is being loaded, not on every store notify.
- The tab bar's close control is a `<span role="button">` **inside** the tab
  `<button>`; a tab with no close control is `permanent`. Chat tabs are permanent, so
  there is nothing to click to close one.
- `~/.cptr/cptr.db` is empty; the live DB is `~/.cptr/app.db`, and declared services
  are not a table there -- they live in `workspaces.data['services']` (JSON), reached
  via `cptr/utils/services.py`.

Probes left in `.cptr/harness/` (gitignored, local only): `sel-services-add.js`,
`sel-service-start.js`, `sel-tab-dash.js`, `sel-tab-chat-close.js`,
`sel-home-continue.js`, `probe-service-act.js`, `probe-home-continue.js`,
`probe-services-add-click.js`.
