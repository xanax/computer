# Chat tab (launcher) + sidebar attention filter

## What changed

**Pinned Chat tab.** A workspace's tab bar is now `dash`, `files`, `chat`, then
open files/editors, then chat conversations (`stores.ts`:
`CHAT_TAB`, `orderWorkspaceTabs`, `isWorkspaceTabPinned`, `isWorkspaceTabVisible`,
`pinWorkspaceGroups`). The chat tab is a **launcher**, not a conversation: it
shows ChatPanel's landing view (input + recent chats).

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

- Bar in a workspace: `["dash","files","chat","mur63143",…]`.
- Sidebar: 4 chat rows across 3 of 33 workspaces — `computer` (unread *Nightly
  home backup* first, then this in-flight chat), `brendan` (unread *Greeting*),
  `AIjly` (active deploy run). All 30 read workspaces show no rows.
- Screenshot: `notes/_scratch/chat-tab-sidebar-filter.png`.

Note: a probe that lands too early reads `chatEnabled === false`
(`/api/chats/models` not resolved yet) and therefore **zero** `.ws-chats` and
zero rows. Wait ~14s before believing an empty sidebar.

Tab labels are printed raw (`dash`, `files`, `chat`) — pre-existing, no `$t()`
call, so `bar.dash`/`bar.chat` locale keys are not needed (`bar.files` is an
unused key already in the locale files).
