# `create_download_link`: hand a workspace file to the browser's machine

## What it does

An LLM chat can now give the user a file to download on **their own computer** (the host
running the browser), rather than only showing file contents in chat:

> *"zip the report and give me a download link"* → a card appears in the chat:

```
┌──────────────────────────────────────────────┐
│ ⬇  Q3 Report.pdf                       Download │
│    412.3 KB · reports/Q3 Report.pdf · application/pdf │
└──────────────────────────────────────────────┘
```

Clicking the card saves the file to the browser's machine. The runtime never writes into
the browser's filesystem on its own: the browser does the saving, with a normal
`Content-Disposition: attachment` response.

## Files touched

| File | Change |
| --- | --- |
| `cptr/utils/tools.py` | New built-in tool `create_download_link` (`approval: allow`, group `files`). |
| `cptr/routers/workspace.py` | `send_file()` grew `download_name`; `GET /api/workspace/files/download` grew a `filename` query param; `_safe_filename` / `_content_disposition` helpers. |
| `cptr/utils/chat_task.py` | `display_file`'s inline card logic extracted into `build_tool_card_item()`, which now also maps `create_download_link → download`. Both agent loops (normal + delegate) call it. |
| `cptr/utils/prompt_templates.py` | One line of tool guidance next to the `display_file` line. |
| `cptr/frontend/src/lib/components/chat/AssistantMessage.svelte` | New `download_item` display item + card markup (`formatSize`, activity label). |
| `CHANGELOG.md` | `[Unreleased] → Added`. |

No new router, so nothing to wire in `app.py` / `routers/__init__.py`.

## Tool contract

`create_download_link(path, name="")` returns the same shape as `display_file`, one string
of JSON:

```json
{
  "type": "download",
  "url": "/api/workspace/files/download?path=%2Fws%2Freport.pdf&filename=Q3+Report.pdf",
  "name": "Q3 Report.pdf",
  "path": "report.pdf",
  "full_path": "/ws/report.pdf",
  "workspace": "/ws",
  "size": 422144,
  "mime_type": "application/pdf"
}
```

* `_fn_to_schema()` sends **only the docstring's first line** to the model, so that line is
  the whole contract the model sees ("Create a download link the user clicks to save a file
  to their own computer."); the rest of the docstring is for humans. `:param` lines *do*
  reach the schema.
* `name` is only put in the URL when it differs from the file's own name, so the common
  case keeps a short URL.
* Errors are plain `"Error: ..."` strings → `build_tool_card_item` returns `None` → no card,
  and the model simply relays the message.

### Deliberate limits

* **Workspace-scoped**, exactly like `display_file`: `_resolve_path` allows the workspace and
  the uploads dir, rejects everything else ("Path outside allowed directories") and `.env`.
  A file elsewhere (`/mnt/c/...`, `~/`) must be copied into the workspace first.
* **Single files.** A directory gets a readable error steering the agent to archive first.
  The message suggests `tar -czf` / python's `zipfile` because **`zip` is not installed** on
  this host — suggesting `zip -r` would send the agent into a dead end.
* Approval is `allow` (it only reads a path and mints a URL; nothing is written or executed).

## Verification

All three layers were exercised; the UI card itself is the only part not driven by a real
browser (no Chrome/Chromium on this host — `browser_navigate` cannot run).

1. **Route** (`notes/_scratch/test_dl_route.py`, 18 checks) — real `download_file()` calls
   plus the OpenAPI spec of the real app object:

   ```
   PASS defaults to the file's name            report.pdf
   PASS honours the filename param             attachment; filename*=utf-8''Q3%20Report.pdf
   PASS non-ascii name decodes back            naïve—data.csv
   PASS hostile name is sanitised              evilx.pdf        ('..\evil"\r\nx.pdf')
   PASS no CR/LF in the header
   PASS empty-after-sanitising falls back      report.pdf
   PASS view does not force a download / keeps the real mime type
   PASS HTTP 404 (missing) / HTTP 400 (directory)
   PASS openapi exposes path + filename (filename optional)
   ```

   Starlette writes non-ASCII *and* spaced names as `filename*=utf-8''…`, which is why the
   test decodes the disposition instead of string-matching it.

2. **Loop + tool** (`notes/_scratch/test_dl_dispatch.py`, 9 checks) — through the real
   `execute_tool()` dispatcher: the tool is reached with injected `__context__`, its JSON
   becomes a `download` card, an error result produces **no** card, the no-workspace gate and
   the disabled-tool setting are honoured, and `display_file` still builds its `file` card
   through the shared helper (the refactor is behaviour-preserving).

3. **Live HTTP** (`notes/_scratch/e2e_download.sh`) — a throwaway server on `:4211` with a
   temp `CPTR_DATA_DIR`, real first-run setup (`/api/auth/setup` with the startup token from
   stdout) and a cookie session:

   ```
   1. download, no filename        200 19 application/octet-stream  attachment; filename="report.pdf"
   2. filename=Q3%20Report.pdf     200 19 ...                       attachment; filename*=utf-8''Q3%20Report.pdf
   3. non-ascii filename           200  8 (correct bytes)          attachment; filename*=utf-8''na%C3%AFve%E2%80%94data.csv
   4. /view (inline) unchanged     200 19 application/pdf          (no content-disposition)
   6. missing file                 404 {"detail":"File not found: ..."}
   ```

   The same script curls the long-running `:4200` for contrast — with the pre-change code it
   answers `attachment; filename="oldcheck-….pdf"` **even when `filename=Q3%20Report.pdf` is
   passed**, i.e. the param was previously ignored.

4. Frontend rebuilt (`npm run build`, adapter-static → `cptr/frontend/build`); the new markup
   is in the shipped bundle (`grep -rl "Prepare download" build/` →
   `_app/immutable/nodes/2.DFehhROo.js`). `ast.parse` clean on all four Python files.

## Follow-ups

* The card is only verified by compilation + the fact that it mirrors the working
  `file_item` card, since no browser is available here.
* The `send_file()` PAM branch (`Runtime.stream_file`) is not covered by the live test — this
  host runs in non-PAM mode.
