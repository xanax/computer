# open_browser — the page lands in the chat's workspace, the user is not moved

A chat in workspace **A** calls `open_browser` while the user is reading
workspace **B**. Three behaviours were tried; only the third is right.

1. **Drop `workspace`** (original). The event carried the chat's workspace all
   along, the client ignored it, and the tab appeared in B — far from the chat
   that asked for it.
2. **Switch to A, then open** (first fix). Correct destination, but it *took the
   wheel*: the user was pulled out of whatever they were doing to look at a page
   they had not asked for, losing their scroll position and their place in the
   chat they were reading. A background agent moving your window is worse than
   a page landing quietly somewhere else.
3. **Write the tab into A's layout; announce it** (now). The page is in A's own
   tab strip, the user's screen never moves, and a notification names A. Going
   there is a click — deliberate, on a gesture, which is the whole difference
   from the tool deciding for them.

## Why the server writes the tab

A tab belongs to a workspace's persisted `groups` in the `workspaces` row. A
workspace the user is *not* looking at has no live client store to add it to, so
`cptr/socket/main.py::emit_open_browser` does it there and passes the ids down:

* `browser_session_id` — the proxy session created for the page;
* `tab_id` — the id of the tab it wrote.

If the user *is* in A, `adoptBrowserTab` (stores.ts) finds that tab on screen and
attaches the session, rather than opening a second tab for the same page. The
client's tab strip is live state seeded from the layout but never re-read, so the
server's copy is otherwise only rediscovered on the next load.

Tab shape is exactly what `openBrowserTab` writes:
`{id, type: 'browser', label, path, browserSessionId}` into the group holding the
pinned `dash`/`files` tabs.

A **home** chat has no workspace and no tab strip: no session is created, both
ids are `""`, and the client falls back to `window.open` / open-where-I-am.

## Everything is best effort

The tool has already probed the URL and decided to show it, so a failure to
write the layout must not turn a working page into an error. `_add_browser_tab_to_workspace`
logs and returns `("", "")`; the event still goes out and the client falls back
to opening it the old way. Re-opening a page already in the workspace also
returns `("", "")` — one page, one tab, however many chats ask for it.

## Gotchas

* **`_add_browser_tab_to_workspace` writes to the real DB.** A test calling
  `emit_open_browser` with a real path (e.g. `/home/brendan/aijly.com`) drops a
  browser tab into the user's actual workspace. This happened for real during
  development; `tests/test_open_browser_workspace.py` now has an **autouse**
  `isolated_db` fixture repointing `db._engine`/`_async_session` at a tmp file.
  Any new test that calls this needs that fixture (it is autouse in that file
  only).
* `test_emit_open_browser_carries_the_workspace` must filter on
  `type == "open_browser"` — a workspace emit now sends **two** events
  (`open_browser` and `workspace_browser_opened`).
* Do not use `/home/brendan/...` paths in this file's tests; use `tmp_path`.

## Tests

`tests/test_open_browser_workspace.py` (13 tests): the tab lands in A's layout
and B's is untouched, a home chat creates no session, a repeat does not stack,
a failed write still emits, the client threads `data.workspace` through,
`openLocalPage` no longer calls `switchToWorkspace` (the regression guard),
`InlineRenderer` still calls `openLocalPage(url)` with no target so a *clicked*
link opens where the user is, and both notification keys exist in all 10 locales
with `{{workspace}}` interpolated.