---
area: browser-tab-routing
title: Browser tab routing and the CDP automation browser
aliases: [open_browser, browser tab, cdp, browser_navigate, target hijack]
updated: 2026-10-04
---

# Browser tab routing and the CDP automation browser

Two separate browsers, routinely confused:

- cptr's **Browser tab** — what the user sees. An iframe loaded *through cptr*,
  which is why a port on this box reaches a user who cannot route to it.
- the **automation browser** — hidden, driven by the `browser_*` tools over CDP.

## Map
- `cptr/utils/tools.py` — `open_browser`, the user-facing tool. Probes a local
  target before opening, so "nothing is listening" is an error, not a blank tab.
- `cptr/socket/main.py` — `emit_open_browser`, pushes the `open_browser` event.
- `cptr/utils/browser/cdp.py` — `CDPClient.connect`, the agent's Chrome.
- `cptr/frontend/src/lib/stores/chat.ts` — `openBrowserFromChat`, the socket handler.
- `cptr/frontend/src/lib/stores.ts` — `openLocalPage`, `openBrowserTab`, `switchToWorkspace`.
- `notes/NOTES-open-browser-tool.md`, `notes/NOTES-local-page-links.md` — older write-ups.

## Facts
- [verified 2026-10-04 cptr/utils/browser/cdp.py] `connect()` used to take the
  **first** `type: "page"` target from `/json/list`. That is a race with the user:
  whichever page Chrome happened to list first got navigated, which is what read
  as "my tab keeps changing itself". It now only ever adopts a target carrying
  its own marker (`OWNED_TARGET_URL`) and otherwise calls `/json/new`.
- [ran 2026-10-04 `pytest tests/test_cdp_target_isolation.py`] The marker is
  percent-encoded in the `/json/new` query. A literal `#` is parsed as a
  fragment of the *request* URL and dropped before it is sent, so every target
  would come back as plain `about:blank` and the reuse check would never match.
- [verified 2026-10-04 cptr/frontend/src/lib/stores/chat.ts] The `open_browser`
  event has always carried `workspace`; the client read `data.url` and
  `data.label` off the same object and silently ignored it. A chat in workspace A
  calling `open_browser` while the user looked at workspace B dropped the tab
  into B.
- [verified 2026-10-04 cptr/utils/tools.py] `open_browser` reads the workspace
  from `__context__["workspace"]`, which is `""` for a home chat. Empty means
  "open where the user is" — **not** "switch somewhere": there is no such
  workspace, and treating `""` as a path would throw the user off the home page.
- [verified 2026-10-04 cptr/frontend/src/routes/+page.svelte] The URL
  (`?workspace=`) is the source of truth for which workspace is active, and it
  loads in a `$effect`. Awaiting `goto()` returns *before* the tabs exist, so
  anything that adds a tab right after a switch must wait on the store, not the
  navigation.
- [verified 2026-10-04 cptr/frontend/src/lib/stores.ts] `loadWorkspace()` is
  idempotent **except** that it re-creates browser sessions for `preview` tabs.
  Calling it speculatively in a poll loop leaks sessions; that is why
  `switchToWorkspace` polls the store first and only falls back to it once.
- [verified 2026-10-04 cptr/frontend/src/lib/stores.ts] `openLocalPage(url, label,
  targetWorkspace)` — the third argument is a **switch**, not a hint. A link the
  user clicked passes nothing and opens where they are; only the `open_browser`
  event passes a target.

## Built
- CDP target isolation. `CDPClient.OWNED_TARGET_URL` marks our own target;
  `_find_owned_target` finds it again on reconnect so repeated tool calls reuse
  one tab instead of piling up. Entry: `CDPClient.connect`.
- Workspace-correct `open_browser`. `switchToWorkspace(path)` in `stores.ts`
  switches and waits; `openLocalPage` calls it; `openBrowserFromChat` passes
  `data.workspace` through.
- Tests: `tests/test_cdp_target_isolation.py` (5, stubs `/json/list` and
  `/json/new` — no Chrome needed) and `tests/test_open_browser_workspace.py` (5,
  socket + frontend-source assertions).

## Decisions
- The CDP fix creates a target rather than guessing at one. A blank tab is
  cheap; hijacking the user's page is not, and there is no signal in `/json/list`
  that reliably distinguishes "mine" from "theirs" other than our own marker.
- `switchToWorkspace` waits on `currentWorkspace`, not on `goto()`. The
  alternative — trusting that navigation implies a loaded workspace — returns
  before the tabs exist, and the tab we are about to add has nowhere to land.
- Kept a `loadWorkspace` fallback after the poll times out, for the case where
  no `+page.svelte` is mounted to run its effect (a socket listener firing
  before first paint).
- Left the inline `InlineRenderer.svelte` call site alone. A markdown link is a
  user gesture about a page they are reading; switching workspace for it would
  be wrong.

## Dead ends
- Patching a module-level `httpx` attribute on `cdp` in tests does nothing:
  `connect()` does `import httpx` *inside* the function body, which resolves
  through `sys.modules`. The test silently drives a real Chrome instead.
  Patch `httpx.AsyncClient` itself.
- `websockets.connect` is awaited, so a stub socket class needs `__await__`;
  returning the instance directly raises `TypeError: 'FakeWebSocket' object
  can't be awaited` before any assertion runs.
- `pytest-asyncio` is not installed here. Use `asyncio.run(...)` in tests;
  `@pytest.mark.asyncio` is silently skipped and the coroutine never runs.
- Simulating the old CDP behaviour by patching the source with a script: the
  `/json/list` fetch has to be re-added by hand or the simulation fails with
  `NameError: name 'targets' is not defined` — which looks like a test bug
  rather than the old behaviour. Always confirm a regression test fails for the
  *assertion* reason, not a setup error.

## Do not redo
- How to stub CDP in tests: `FakeHTTP` with `get`/`put`, patched at
  `httpx.AsyncClient`, plus a `__await__`-able `FakeWebSocket`. Written in
  `tests/test_cdp_target_isolation.py`; copy it.
- The `_local_path` / `_serve_hint` pair in `tools.py` already answers "that is
  a path, not a URL" with a command to serve it. Do not add a second check.
- Minting a cookie for a probe is `.venv/bin/python .cptr/harness/mint-cookie.py`
  (see NOTES-sidebar-chat-persist.md for the user-vs-owner trap).

## Open
- Does the automation Chrome still start a visible window? It is supposed to be
  headless; the flag was not re-checked after the target-isolation change.
- `preview`-tab session re-creation in `loadWorkspace` is the remaining reason
  a redundant call is expensive. Worth making idempotent at the source.
