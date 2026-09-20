# open_browser — show the page in cptr's own Browser tab

`cptr/utils/tools.py::open_browser` used to guess between an in-app tab and the
user's desktop browser (`where` = auto/app/system/both) and could launch a real
browser process via `cptr/utils/browser/opener.py`. On a headless server that
window is one nobody can see, so the tool could report success while the user
saw nothing.

Now it does exactly one thing: **adds a Browser tab to the cptr window the user
already has open** (`emit_open_browser` → socket `events:chat` type
`open_browser` → `stores/chat.ts::openBrowserFromChat` → `openBrowserTab`). The
tab loads the page *through* cptr's proxy, so a process started on the server
(a port on `localhost`) reaches the user's screen even when their own machine
cannot route to the box.

## Shape of the call

* `url` accepts the old shorthands: `8765`, `localhost:8765/x`, `example.com`.
* `label` names the tab; defaults to `host:port`.
* `where` is **ignored** (keyword-only, so it never reaches the model schema) —
  an old transcript replaying `open_browser(..., where="system")` must not fail.

## Failures it refuses to hand the user

* a **path** instead of a URL (`dist/index.html`) → error + the
  `python3 -m http.server` command that would serve it (`_serve_hint`);
* a **`file://` URL** → same, because the tab proxies http(s) only;
* **nothing listening** on a local port → 3 quick probes, then an error asking
  the model to start the process first (`_local_page_status` only counts a
  refused/timed-out *connection* as "not serving"; a 404/500/redirect still
  opens, because that is the page's own business);
* **no cptr window connected** (`is_user_active`) → error + the URL to paste.

Success returns `{success, url, label, probe, opened_in: "cptr Browser tab"}`.

The client still needs a workspace open before it can add a tab: with
`currentWorkspace` unset it falls back to `window.open` (which a socket-driven,
non-click context usually has blocked). That is why live verification navigates
to `/?workspace=<path>` first.

## Verification

Unit: `notes/_scratch/test_open_browser.py` (23 assertions — error paths, serve
hints, probing, one socket event, no desktop launch, legacy `where` ignored).

End-to-end, without touching the live server (`cptr-pid.txt` and the real chat
history stay clean):

1. `cp -a ~/.cptr /tmp/cptr-obtest` (data dir is a *copy*: chats written during
   the test land in the copy and vanish with it).
2. `cd /tmp && CPTR_DATA_DIR=/tmp/cptr-obtest PYTHONPATH=/home/brendan/computer \
   setsid nohup /home/brendan/computer/.venv/bin/python -m cptr.cli run \
   --host 127.0.0.1 --port 4210 --headless &` — `cwd=/tmp` keeps pid/log files
   out of the repo.
3. `.venv/bin/python .cptr/harness/mint-cookie.py --data-dir /tmp/cptr-obtest`
4. `node .cptr/harness/cdp.mjs --url "http://127.0.0.1:4210/?workspace=%2Fhome%2Fbrendan%2Fcomputer" --js .cptr/harness/open-browser-e2e.js`

The harness posts a real chat turn (`POST /api/chats`, note the plural) asking
the model to call the tool, then waits for a tab labelled `Inbuilt browser demo`
whose iframe (`/api/browser/frame/<sid>/http/localhost%3A8793/`) renders
`harness-marker-ok` from the throwaway server on :8793.

Result: tab opened, page rendered, one browser session created —
`notes/_scratch/e2e-open-browser-tab.png`. Afterwards: kill the :4210 process,
`rm -rf /tmp/cptr-obtest`, re-mint the cookie for `~/.cptr`.

## Live re-check on the real server (:4200, after the 15:22 restart)

Cheap variant that needs no throwaway lane and no chat turn: the CDP page is just
another client for the same user, so firing the tool *from a running chat* reaches
it too (`.cptr/harness/open-browser-wait.js` — boots, opens the workspace, then
waits 75 s for the event).

    LD_LIBRARY_PATH=/home/brendan/.cache/ms-playwright/host-libs node .cptr/harness/cdp.mjs \
      --url 'http://127.0.0.1:4200/?workspace=%2Fhome%2Fbrendan%2Fcomputer' \
      --js .cptr/harness/open-browser-wait.js --wait 5000 \
      --shot notes/_scratch/open-browser-live.png

`--url` needs the scheme — passing `127.0.0.1:4200/...` fails with
`Cannot navigate to invalid URL`. Fire

    open_browser(url='http://localhost:8793/', label='Inbuilt browser demo')

about 15 s into the run. Result: `tabsAfter: ["computer", "Inbuilt browser demo"]`,
`frameSrc: /api/browser/frame/<sid>/http/localhost%3A8793/`, `frameRendered: true`
(`harness-marker-ok`), screenshot above. Dead port (`localhost:8799`) returns
`success:false` + "start the process first" hint and opens nothing.

The frame is **not** a headless browser: `POST /api/browser/sessions` + the
`/api/browser/frame/...` route is an httpx proxy that rewrites HTML/CSS/JS
(`cptr/routers/browser.py`); the server spawns no chrome/playwright child. The
old CDP/opener code in `cptr/utils/browser/` is for the separate
"attach to a Chrome debug port" feature.

## Leftovers

* `cptr/utils/browser/opener.py` is now unused (upstream file, kept: deleting it
  would only buy merge conflicts). Nothing else launched a desktop browser.
* The rewrite is Python-only, so the live :4200 server needs a restart to pick
  it up; the frontend bundle already contains the working handler (`npm run
  build` not required).
