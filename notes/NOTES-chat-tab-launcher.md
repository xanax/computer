# Chat tab (launcher) + sidebar attention filter

## What changed

**Pinned Chat tab.** A workspace's tab bar is now `dash`, `files`, `chat`, then
the open files, terminals and other editors — **and nothing else** (`stores.ts`:
`CHAT_TAB`, `orderWorkspaceTabs`, `isWorkspaceTabPinned`, `isWorkspaceTabVisible`,
`pinWorkspaceGroups`). The chat tab is a **launcher**, not a conversation: it
shows ChatPanel's landing view (input + recent chats).

**No chat is ever a tab.** `isWorkspaceTabVisible(tab)` is `tab.type !== 'chat'
|| !!tab.permanent`: the launcher is the only chat tab the bar keeps. A
conversation still exists as a tab in the data model, because that is what keeps
its ChatPanel mounted (`+page.svelte`, `.persisted-tab`) and lets the workspace
remember which chat is open — it is just never drawn in the bar. Open a
conversation from the sidebar (`openChat`), from the launcher's history, or from
`openChatTab(id)`; each reuses the chat's hidden tab instead of adding one.

Consequence: while a conversation is showing, no *bar* button is active (the old
behaviour, before chats were surfaced as tabs). `nextTab`/`prevTab` now enter the
bar at the end the key heads for instead of doing nothing, since they cycle
`isWorkspaceTabVisible` and the active tab may not be in that list.

`detachPinnedChatTab(tabId)` — when the launcher gets a real conversation (a send
from it, or a chat picked from its history list) the tab becomes that
conversation's tab and a *fresh* launcher takes the pinned slot. The conversation
keeps its tab id, so the ChatPanel is not remounted mid-send. `openChatTab()`
with no id now focuses the launcher instead of stacking a second blank tab.
`GroupTabBar` hides the `new-`/`pending-` placeholder (it is the same tab, about
to be named) — that is what `isWorkspaceTabVisible` is for.

**Sidebar = attention list.** `SidebarWorkspaceList.visibleChatsFor()` keeps a
chat only while the server is working on it (`status.active`) or it is unread
(`isChatUnread`). Everything else lives in the launcher's history list. The first
fetch is `WS_CHATS_INITIAL_FETCH = 25` because a chat waiting unread can sit
further down the `updated_at` order than the few read chats opened since.

Ordering: `byAttention()` — unread (waiting on the user) above in-flight, newest
first within each. Applied in `visibleChatsFor()`, **not** in the fetch, because
the paged fetch returns server order and only the append path sorts.

## Verified (CDP, port 4200 + 9333)

- Bar in a workspace: `["dash","files","chat","mur63143","mur6c65j"]` — labels
  `dash`, `files`, `chat`, then two *file* tabs (an abbeyfield page, the AI
  Profile Orchestrator); no chat title anywhere.
- Sidebar click on *Add Chat Tab Beside Dash and Files* opened the conversation
  (pane text = the chat's own first lines) with the bar unchanged — no tab added.
- Clicking the pinned `chat` tab returns to the launcher: visible pane = "What
  can I help you with?" + history (`Nightly home backup 12h`, `Add Chat Tab…`).
- Sidebar: 4 chat rows across 3 of 33 workspaces — `computer` (unread *Nightly
  home backup* first, then this in-flight chat), `brendan` (unread *Greeting*),
  `AIjly` (active deploy run). All 30 read workspaces show no rows.
- Screenshots: `notes/_scratch/chat-tab-sidebar-filter.png`,
  `notes/_scratch/bar-no-chat-tabs.png` (bar + launcher, sidebar group expanded).
- Probes: `notes/_scratch/probe-bar-no-chat-tabs.mjs`,
  `notes/_scratch/probe-sidebar-chat-no-tab.mjs`. Sidebar chat rows are
  `DIV.chat-item` under `.ws-chats` (not `<button>`), and the group must be
  expanded via `.ws-icon-toggle` — chats paint ~4s after the toggle.

Note: a probe that lands too early reads `chatEnabled === false`
(`/api/chats/models` not resolved yet) and therefore **zero** `.ws-chats` and
zero rows. Wait ~14s before believing an empty sidebar.

Tab labels are printed raw (`dash`, `files`, `chat`) — pre-existing, no `$t()`
call, so `bar.dash`/`bar.chat` locale keys are not needed (`bar.files` is an
unused key already in the locale files).
