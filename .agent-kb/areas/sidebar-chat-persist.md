# Sidebar chat rows: persist until closed

A chat keeps its per-workspace sidebar row for as long as `closed_at` is null.
Reading it does not remove it; only the `x` does. Previously the list was an
*attention* list (`status.active || isChatUnread`), so a read chat vanished while
still open.

## The predicate is now one field

```ts
// SidebarWorkspaceList.svelte
function needsAttention(chat: ChatInfo): boolean {
	return !chat.closed_at;
}
```

Two consequences that are easy to get wrong:

- **Sort is no longer attention-ordered.** It used to be `waiting(b) - waiting(a) ||
  b.updated_at - a.updated_at` (unread first). With read chats held, that
  reordering only made them jump around, so it is now plain `updated_at` desc,
  in both `visibleChatsFor` and the socket-merge branch. A chat the server is
  working on carries a spinner instead of a dot.
- **The fetch must ask for closed chats.** `getChats(..., include_closed=true)`.
  The endpoint's own `include_closed=false` is an attention rule — it lets a
  closed chat back into the list once new activity makes it unread — which is
  exactly what "closing is the one and only way a row leaves" forbids. So the
  request says `true` and the `closed_at` filter is done client-side.
  (`cptr/routers/chat.py:_is_hidden_closed_chat`, line 31.)

## No per-workspace "Show more", one tail "Show more"

`WS_CHATS_PAGE_SIZE`/`wsChatsHasMore`/`.ws-chat-show-more` are gone; there is
nothing to page, so `WS_CHATS_LIMIT = 200` (the endpoint's `le=200` cap, above
every per-workspace chat count here) in a single request. The **all-workspaces**
tail toggle is separate and still exists as `.ws-show-more` — different element,
different purpose, and it is the only "Show more" left in the sidebar.

Opening a chat no longer reopens a closed one. That block existed only because
closed chats could be *visible* (unread-but-closed); now they cannot.

## Verifying it (and four ways to get a false answer)

`bash notes/kb-evidence/verify-sidebar-persist-until-closed.sh 4200`

1. **Mint for the chat owner, not the signed-in user.** `/api/chats` returns
   `{"chats":[]}` for a valid session whose `user_id` owns nothing. On this box
   `xanax` owns all 399 chats; `brendan` owns none — a `brendan` cookie gives a
   perfect-looking "0 rows everywhere", identical to a broken feature.
2. **`limit=500` is a 422, and it reads as "zero chats".** The error body has no
   `chats` key. Page until `has_more` is false instead.
3. **Join rows by `getAttribute('title')`, never `innerText`.** `ChatItem`
   renders `title={chat.title}` (`components/common/ChatItem.svelte`), so the
   attribute is the stored title. `innerText` has already had markdown links
   and the relative-time strip applied — `[remote-cmd](file://remote-cmd)
   create screenshot` renders as just "remote-cmd". An innerText-joining guard
   reported 10 chats "missing" while the counts were exactly equal (30/30).
4. **A collapsed workspace has no `.ws-chats` container**, so it contributes zero
   rows. Comparing counts across all workspaces conflates "collapsed" with
   "filtered out". Assert only over workspaces where the list is rendered.

## The harness wedge

A leftover tab on the harness browser (port 9333, own profile) makes **every**
probe time out at `Runtime.evaluate`, including a trivial one, while
`/json/version` still answers 200. Diagnose with
`curl -s http://127.0.0.1:9333/json/list` — if the page is not yours, close it:

    curl -s http://127.0.0.1:9333/json/list   # -> the other session's page
    curl -s http://127.0.0.1:9333/json/close/<id>

Wiping `/tmp/verify-chrome` also works but loses the session cookie (re-mint).
Do **not** `pkill -f chromium-1243` from `run_command`: the pattern matches the
invoking shell and kills it (exit -9).

## Closing is reversible, but not over HTTP

`setChatClosed` is a **socket** emit (`chat:closed`), not a REST route — a
`POST /api/chats/{id}/close` returns `405 Method Not Allowed`. To restore a chat
closed by a probe:

```python
c = await Chat.get_by_id(chat_id)     # Chat.get does not exist; get_by_id does
await Chat.set_closed_at(chat_id, c.user_id, None)   # static, 3 args, not an
                                                      # instance method
```

`Chat.closed_at` is an `InstrumentedAttribute`, not a callable — `await
Chat.closed_at(id)` is a `TypeError`.

## Unrelated trap hit while probing

`--wait` is not the settle time; rows take 15-20s to paint *after* navigation.
Budget `--wait 28000`, and note that a probe doing serial per-workspace paging
plus an `all.some()` inside a filter is O(n^2) and blows the 120s
`Runtime.evaluate` budget — collect the DOM first, then a single
`Promise.all` over the fetches.