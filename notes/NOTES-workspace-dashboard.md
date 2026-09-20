# Workspace-name click → dashboard

Clicking a workspace name in the sidebar used to `goto('/?workspace=…')`, which
restored the persisted editor tabs (i.e. "opens the last chat"). Now it adds
`&view=dashboard`, and `+page.svelte` renders a new `WorkspaceDashboard` pane
instead of the editor when that param is present.

## Where

- `SidebarWorkspaceList.svelte` — `openWorkspace()` + the workspace `<a href>` now
  append `&view=dashboard`.
- `routes/+page.svelte` — new `{:else if $page.url.searchParams.get('view') === 'dashboard'}`
  branch between the `!$currentWorkspace` home pane and the editor layout.
- `lib/components/WorkspaceDashboard.svelte` (new) — dashboard pane.
- `lib/i18n/locales/en.json` — `dashboard.*` keys (title, backToWorkspace, newChat,
  manageScheduled, upcoming, noUpcoming, createScheduledHint, recentChats, noChats,
  now + minutes/hours/days plural forms).

## Behaviour

- Shows the workspace display name + full path, a "Back to workspace" arrow
  (→ `/?workspace=…` without `view`), "New chat", and "Manage scheduled tasks"
  (→ `/scheduled`, the existing AutomationsPanel).
- "Upcoming scheduled tasks": active automations for this workspace, sorted by
  `next_run_at` (ns), rendered as `Mon D, HH:MM · in Xm/h/d`. Empty state links to
  create one.
- "Recent chats": 8 most recently updated open chats via `getChats(ws, 8, 0,
  'updated_at', 'desc', false)`; row opens the chat tab then navigates to the
  editor view. `openChatTab(chatId)` reuses an existing tab when present.

## Styling notes

Pure CSS with `--app-bg/fg/fg-muted/border/divider/hover` so the mono (bw/bw-dark)
e-ink themes inherit the ink-on-paper look: primary button = solid ink fill with
paper text (inverts on hover), cards = paper with ink border, empty state = dashed
ink border, no greys beyond `--app-fg-muted`. `manageScheduled`/"New chat" are
always-visible header buttons (no hover-only affordances).

## Verification

`npm run build` clean. `getAutomations(ws)` → `{items,total}`; `getChats` →
`{chats,total,has_more}` shapes confirmed against the live API. Icons used
(`arrow-left`, `clock`, `plus`, `chat-bubble`) all exist. svelte-check adds no
new diagnostics beyond the pervasive pre-existing `'$t' is of type 'unknown'`.
Browser smoke-test not run: local headless Chromium is missing `libnspr4.so` /
`libnss3.so` and there's no sudo to install them.
