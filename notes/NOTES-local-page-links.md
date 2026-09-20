# Local page links open in cptr's Browser tab — verified end to end

Chat markdown links to the machine cptr runs on now load in cptr's own Browser
tab instead of a new browser window. That tab's page is fetched by the server
and handed over through `/api/browser/frame/...`, so `localhost:5173` works even
when the user's browser cannot route to the box (WSL, container, remote host).

Code:

| file | job |
| --- | --- |
| `lib/utils/localPage.ts` | recognises a local page: loopback hosts, normalised URL, label, and `findBareLocalPage` for bare `localhost:8793` / `127.0.0.1:8000/api`. |
| `lib/utils/localPageLink.ts` | marked inline tokenizer extension: bare loopback references become ordinary `link` tokens (GFM only autolinks URLs with a scheme). `localPageLexerOptions` spreads `marked.defaults` — a fresh `Lexer(options)` does not merge them, and dropping `gfm` silently kills tables/autolinks. |
| `InlineRenderer.svelte` | local links render dotted-underlined and call `openLocalPage`; modified clicks (ctrl/cmd/shift/alt) are left to the real browser. |
| `stores.ts` `openLocalPage` | opens/reuses the Browser tab; with no workspace open falls back to `window.open`. |
| `stores/chat.ts` | the socket's `open_browser` event routes through `openLocalPage` too. |
| i18n `markdown.openLocalInBrowser` | title text, e.g. "Opens localhost:8793 in the Browser tab". |

## Evidence (server 4210, headless CDP harness)

Fixture: `/tmp/openbrowser-demo` served on 8793 by
`python3 -m http.server 8793 --directory /tmp/openbrowser-demo` (index page is
`<h1>harness-marker-ok</h1>`, plus `api/health/` for the path case).

Workspace state was cut down to one chat tab (no browser tab), so a Browser tab
appearing after the click can only come from the click:

| link in the chat | result |
| --- | --- |
| `localhost:8793` (bare) | new tab `localhost:8793`; frame `/api/browser/frame/<id>/http/localhost%3A8793/`; text `harness-marker-ok`; server log `GET / 200`. |
| `http://127.0.0.1:8793/?probe=1` (explicit) | new tab `127.0.0.1:8793`; frame keeps `?probe=1`; marker; log `GET /?probe=1 200`. |
| `127.0.0.1:8793/api/health` (bare + path) | new tab; frame text `harness-marker-ok\npath=/api/health`; log `301` then `GET /api/health/ 200`. |
| `` `localhost:8793` `` (code span) | stays a literal code span, no anchor. |
| `https://example.com/` | untouched, `target="_blank"`. |

Screenshots: `notes/_scratch/lpe2e-click-{bare,explicit,path}.png`.

## Re-running it

```bash
TOKEN=$(awk -F'\t' '/cptr_session/ {print $7}' /tmp/verify-cookies.txt)   # mint-cookie.py
# 1. strip the workspace down to one chat tab (see /tmp/ws-test-state.json)
curl -s -X PUT -H "Cookie: cptr_session=$TOKEN" -H 'Content-Type: application/json' \
  --data-binary @/tmp/ws-test-state.json \
  "http://127.0.0.1:4210/api/state/workspace?path=%2Fhome%2Fbrendan%2Fcomputer"
# 2. baseline: links present, no browser frames
LD_LIBRARY_PATH=/home/brendan/.cache/ms-playwright/host-libs node .cptr/harness/cdp.mjs \
  --url 'http://127.0.0.1:4210/?workspace=%2Fhome%2Fbrendan%2Fcomputer' --wait 9000 \
  --js notes/_scratch/lpe2e-verify.js
# 3. click one link, then verify again
LD_LIBRARY_PATH=/home/brendan/.cache/ms-playwright/host-libs node .cptr/harness/cdp.mjs \
  --url 'http://127.0.0.1:4210/?workspace=%2Fhome%2Fbrendan%2Fcomputer' --wait 9000 \
  --click-on notes/_scratch/lpe2e-target-bare.js --click-wait 5000 \
  --js notes/_scratch/lpe2e-verify.js --shot notes/_scratch/lpe2e-click-bare.png
```

`lpe2e-target-{bare,explicit,path}.js` pick the anchor by href, scroll it to the
middle, and return `hitSelf()` so the harness refuses to click a stray element.
The fixture message is the last assistant message of chat
`38ae6d29-c203-42ee-bc81-fb93540ff64c` (created through
`POST /api/chats/<id>/messages`). Put the workspace state back afterwards.

## Traps worth remembering

- **`visibility: hidden` keeps layout.** `.persisted-tab-hidden` still returns
  client rects, so an anchor found by `getClientRects()` may be a copy inside a
  hidden tab. Every DOM probe has to test `getComputedStyle(el).visibility`
  (see `shown()` in `lpe2e-verify.js`), otherwise a click lands on whatever
  overlaps the invisible copy and nothing happens.
- **`?chatId=` is stripped.** Opening the app with an intent param is not a way
  to land on a chat: within a second the URL loses it and the workspace
  dashboard shows. Persisting `activeTabId` in the workspace state is the
  reliable route.
- **A POSTed message needs a real `parent_id`.** The client renders the branch
  from `current_message_id` back through `parent_id`; a dangling parent makes the
  message invisible in the chat even though the API returns it (that is how the
  first fixture message was lost).
- **An `await` in a `--js` script used to be a SyntaxError.** `cdp.mjs` wrapped
  the body in a *sync* IIFE, so any top-level `await` (every polling probe) threw
  and the harness still exited 0 with no JSON — a failed probe looked like a
  probe that found nothing. It now wraps the body in `(async () => { ... })()`
  (cdp.mjs:285).
