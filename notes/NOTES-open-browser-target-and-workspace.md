# NOTES — open_browser: CDP target hijack + wrong-workspace tab

Two separate bugs, both of which surface to the user as "my page changed itself"
or "the page opened in the wrong place". Reported from the dwp app chat
(70167e1d), where the browser tab "kept getting hijacked by another page".

## Bug 1 — CDPClient.connect adopted the first page target

`cptr/utils/browser/cdp.py`, `CDPClient.connect`.

Old behaviour: GET `/json/list`, take the first entry with `type == "page"`,
attach to it.

That is a race with the user. The automation Chrome and the user's own tabs live
in the same browser, and `/json/list` order is not stable and carries no
ownership information. So `browser_navigate` navigated *whatever page happened to
be first* — the symptom being "the tab keeps changing itself".

Fix: never adopt a target we did not create.

- `OWNED_TARGET_URL` — a marker string (percent-encoded in the `/json/new`
  query; a literal `#` is parsed as a fragment of the request URL and dropped
  before it is sent, leaving every target plain `about:blank`).
- `_find_owned_target(base)` — finds our own target if one is still there, so a
  reconnect reuses it instead of opening another tab per tool call.
- `_create_target(base)` — otherwise opens one of ours.

## Bug 2 — open_browser dropped the workspace

`cptr/frontend/src/lib/stores/chat.ts`, `openBrowserFromChat`.

The socket event has always carried `workspace`. The handler read `data.url` and
`data.label` off the same object and ignored the field next to them, so a chat in
workspace A calling `open_browser` while the user looked at workspace B opened
the tab into B.

Fix:

- `openBrowserFromChat(url, label, workspace)` → passes it down.
- `openLocalPage(url, label?, targetWorkspace?)` in `stores.ts` — the third
  argument is a **switch**, not a hint.
- `switchToWorkspace(path)` — added. It cannot just `goto()` and continue: the
  URL is the source of truth (`?workspace=`) and `+page.svelte` loads it in a
  `$effect`, so `await goto()` returns *before* the tabs exist. It polls
  `currentWorkspace` (3s / 50ms) and falls back to `loadWorkspace()` once.

The fallback is deliberately last: `loadWorkspace()` is idempotent **except**
that it re-creates browser sessions for `preview` tabs, so calling it in a poll
loop leaks sessions.

### The empty-workspace case

`open_browser` reads `__context__["workspace"]`, which is `""` for a home chat.
Empty must mean "open where the user is". Switching on `""` would throw the user
off the home page. This is why the field is a *switch* and not a path.

## Tests

- `tests/test_cdp_target_isolation.py` — 5 tests, stubs `/json/list` and
  `/json/new`, no Chrome needed. Covers: never adopt a user page; ordering does
  not decide ownership; reuse the target we created; marker survives encoding;
  a user's `about:blank` is not mistaken for ours.
- `tests/test_open_browser_workspace.py` — 5 tests: the event carries the
  workspace, a home chat sends `""`, the tool forwards `__context__`, and two
  frontend-source assertions that the client threads it through.

Both suites were verified to **fail against the old behaviour** before being
accepted. Re-run that check when changing either fix.

## Traps hit while testing this

1. `connect()` does `import httpx` **inside** the function body. Patching a
   module-level `httpx` attribute on `cdp` does nothing — the test silently
   drives a real Chrome. Patch `httpx.AsyncClient` itself.
2. `websockets.connect` is awaited: a stub socket class needs `__await__`, or
   `TypeError: 'FakeWebSocket' object can't be awaited` fires before any
   assertion runs.
3. `pytest-asyncio` is not installed. `@pytest.mark.asyncio` is silently
   skipped and the coroutine never runs. Use `asyncio.run(...)`.
4. When simulating the old behaviour by patching source with a script, the
   `/json/list` fetch must be re-added by hand, or the simulation fails with
   `NameError: name 'targets' is not defined` — which reads like a broken test
   rather than the bug. Always confirm a regression test fails on its
   *assertion*.

## Not changed

`InlineRenderer.svelte` calls `openLocalPage(url)` with no target. That is right:
a markdown link is a user gesture about a page they are reading, so it opens
where they are.