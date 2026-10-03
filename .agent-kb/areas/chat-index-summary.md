---
area: chat-index-summary
title: The chat index lives in chats.summary — free to fill, and how to fill it without wrecking search
aliases: [chats.summary, chat index, backfill, search_chats, search ranking, summary, checkpoint, chat_summary, index button]
updated: 2026-10-03
---

# The chat index lives in `chats.summary` — free to fill, and how to fill it without wrecking search

## Map
- `cptr/models/chats.py:503` — `Chat.search_by_text`, the ranker. Scores id / title / **summary** / message
  content; `summary` sits at **rank 40, above raw message content** (50+).
- Already-read surfaces, so filling it is visible everywhere at once: `routers/chat.py:202` (chat list),
  `chat.py:706` (single chat), `routers/search.py:92` (`/api/search`), `utils/tools.py:3268` (`search_chats` browse).
- `cptr/models/chats.py:196` — `Chat.update_summary(chat_id, summary, updated_at=0)`. Pre-existing writer; fork uses
  it at `routers/chat.py:1160`.
- `chat_messages.chat_summary` (migration `0003_add_context_compaction.py`) — the compaction checkpoint, a *different*
  field on a message row. See `notes/NOTES-context-compaction-newest-checkpoint.md`.
- `cptr/utils/chat_index.py` — **the** entry builder: `build_entry(messages, workspace) -> (text, kind)`, `IndexMessage`,
  `TOOL_CALL_RE`. Used by *both* the backfill and the endpoint, so the two cannot drift.
- `scripts/backfill-chat-index.py` — the backfill (dry run by default). Imports the module above.
- `cptr/routers/chat.py:1440` — `POST /api/chats/{id}/index` → `index_chat`, the per-chat refresh. Reads nothing
  from the request body, so it is safe to call in-process with `_get_user` stubbed (see below).
- `cptr/frontend/src/lib/components/chat/ChatInput.svelte` — the `/index` slash-command row; `ChatPanel.svelte`
  `handleIndexChat` is the handler. Keys `chat.commandIndex*` / `chat.index*`.
- `scripts/add-chat-index-locales.py` — adds those keys to all 10 locales by **text insertion**, `--check` to verify.
- `notes/NOTES-chat-storage-and-search-audit.md` — where the bytes are, and the blind spot this fixes.

## Facts
- [ran 2026-10-03 ~/.cptr/app.db] `chats.summary` was populated for **0 of 378** chats, while already being ranked
  and returned everywhere. Filling it needed no schema change and no new queries.
- [ran 2026-10-03] After the backfill: **378/378** — 120 with a real prose summary, 258 composed. 0 skipped, because
  every chat has at least one user message.
- [ran 2026-10-03] The reason a free index is worth anything: 56 MB of the 58 MB `chat_messages` table is tool
  activity, and tool activity is **invisible to search and read** (`_shape_chat_tool_message` returns no tool calls
  and no outputs). File paths from `function_call.arguments` put a searchable handle on that 96% for the first time.
- [ran 2026-10-03] `function_call.arguments` is where the free signal is: `{'path': 'README.md'}` etc. Extract keys
  containing `path`/`file`, including `list` and nested `dict` values.
- [ran 2026-10-03] Relativising paths against `meta.workspace` keeps 9,812 values and drops 446 (4%) — the drops are
  out-of-workspace absolutes. Worth keeping: the filter is not costing real signal.
- [verified 2026-10-03 ~/.cptr/app.db] **No triggers exist on `chats`**, so a plain `UPDATE chats SET summary` cannot
  move `updated_at`. This matters because the sidebar and `/api/search/recent` sort on `updated_at`.
- [ran 2026-10-03] Re-running the script writes 0 rows and reports `skipped 378` — it skips any chat that already has
  a summary unless `--force`. Idempotent by default.
- [ran 2026-10-03] No entry reached the 1,600-char cap, so nothing is being silently truncated today. Avg 582 chars.
  131 composed entries have no path line (tool calls with no path-ish args, e.g. agent-tool-heavy chats).
- [verified 2026-10-03 cptr/env.py:58] DB is `$CPTR_DATA_DIR/app.db`, default `~/.cptr/app.db`; server holds it open in
  WAL. One `BEGIN IMMEDIATE` with `pragma busy_timeout=30000` coexists with the live server.
- [verified 2026-10-03 cptr/models/chats.py:197] **`update_summary` writes `updated_at` verbatim** —
  `.values(summary=..., updated_at=updated_at)`. Its `updated_at: int = 0` default therefore **zeroes** the
  timestamp; it does *not* mean "leave it". Both live callers pass an explicit value (`chat.py:1160` fork → `now`,
  `chat.py:1469` index → `chat.updated_at`), so no row is wrong today. Pass it explicitly or the sidebar sort breaks.
- [ran 2026-10-03] **`ChatMessage.output` is `Column(JSON)`, not text.** Through the ORM it arrives as a decoded
  `list`; through a raw `sqlite3` connection it is the stored string. Code that assumes one shape silently produces an
  entry with **no tool activity at all** — a plausible-looking wrong index, not an error. `build_entry` handles both
  (re-`json.loads` a `str`, pass a list through); do not drop that branch.
- [ran 2026-10-03] Branch coverage over the real corpus: **258 `composed`, 120 `checkpoint`, 0 `empty`** — no chat has
  zero messages, so the `empty` branch is only reachable via `build_entry([])` and is unit-tested, not live-tested.

## Built
- **Phase 1 (done):** `scripts/backfill-chat-index.py` filled all 378 chats. Idempotent; `--force` to rewrite;
  `--dry-run` default.
- **Phase 2 (built, pending restart):** `POST /api/chats/{id}/index` (`cptr/routers/chat.py`) →
  `{ok, indexed, kind, summary, chars}`; `indexed: false` with `reason: "empty"` when there is nothing to index;
  404 for an unknown chat. Wire-up: `/index` in the composer (`ChatInput.svelte`) → `indexChat()` in
  `lib/apis/chat.ts` → `handleIndexChat` in `ChatPanel.svelte`, which toasts and reloads the chat.
- **Endpoint verified in-process** against a snapshot copy, 9/9: summary persisted, `updated_at` untouched,
  `kind` reported, and — the one that matters — the endpoint's entry is **byte-identical to `build_entry`** for the
  same chat. Exercised live on a real `composed` chat and a real `checkpoint` chat, plus the 404 and `empty` paths.

## Decisions
- **Two omissions in the composed form, both deliberate: no English filler labels and no dates.** `summary` is
  *substring*-matched by the ranker, so a literal `Files:` or a year present in all 378 rows would make every chat
  match the query "files" or "2026" and flood results. A distinctive token (`messageParser`, `mono-codemirror`) does
  now resolve to its chat, verified against the DB. Keep this property if the format is ever changed.
- No LLM calls anywhere in the backfill. A checkpoint's prose is reused verbatim; everything else is extracted.
- Precedence: checkpoint prose → else opening question + paths + tool counts.
- A checkpoint describes only the messages dropped **before** it, so the paths touched **after** it are appended as a
  second line. Cheaper and better than the plan's fall-through-when-later-activity heuristic.
- Write the chat's **existing** `updated_at`, never `now` — refreshing an index must not reorder the sidebar. Note the
  `update_summary` default of `0` does *not* do this for you; it writes `0` (see Facts).
- **The `/index` button is free-only: no model is called.** It rebuilds the same entry the backfill writes, from data
  already on disk, so it cannot fail on a missing or broken utility model, costs nothing, and is safe on every click.
  Rejected: routing the button through a utility-model call — that would make an explicitly manual refresh the one
  action that can fail, for a field whose whole value is being filled for free. If an LLM summary is ever wanted it
  belongs as a *separate*, opt-in concern, not behind this button.
- Titles are *not* copied into the composed entry — the ranker already scores title separately.

## Dead ends
- Looking for a new table, FTS index or export file. All of it already exists: one field, already ranked.
- Treating the 9 chats with no `workspace` in `meta` as a bug — paths simply arrive absolute and get dropped; those
  chats still get a question and a tool line.
- "Fixing" the short entries: 9 chats whose entire content is "hi" produce `summary == title`. That is an honest
  index of that chat, not a defect. Do not add a special case.

## Open
- **Phase 2 is built and verified, but not yet live.** The new route is not served until the process restarts; the
  frontend is already compiled (`npm run build` → `build/`). Restart is the user's call — the assistant does not
  restart the server. Until then `POST /api/chats/{id}/index` returns 404 from the running process.
- `loadChat(chatId)` after a successful index re-renders the panel; on a long chat this is a full reload for a field
  the visible UI does not display. Acceptable now, but it is the obvious thing to trim if it ever feels slow.
- Forks inherit the index: `chat.py:1160` copies `summary` on fork, so a fork shows its parent's entry until
  something refreshes it.
- Searching now ranks differently — this is the one behaviour change to an existing feature. `_extract_snippet` can
  return summary text as the snippet.
- No portability: the DB is outside every repo and not versioned; `scripts/cptr-backup.sh` captures `app.db`
  WAL-safe nightly, so it survives disk loss but does not travel with a clone.

## Do not redo
- Re-measuring "is `chats.summary` empty" or "where is it read/ranked" — the map above is current as of 2026-10-03.
- Looking for a `sqlite3` CLI on this box: there is none. Use `.venv/bin/python` with the `sqlite3` module.
- Re-deriving that `output` needs both shapes handled, or that `update_summary` must be passed `updated_at`. Both are
  in Facts with the code that proves them.
- Writing a *second* builder for the endpoint. `cptr/utils/chat_index.py` is shared on purpose; the backfill and the
  button agreeing byte-for-byte is the property that makes the button trustworthy. There is a test for it (below).
- Testing the endpoint by restarting the server. Call the handler in-process instead: snapshot the DB with
  `sqlite3.Connection.backup` into `$CPTR_DATA_DIR=/tmp/...`, stub `cptr.routers.chat._get_user`, `await
  chat.index_chat(None, chat_id)`. Verifies the real code path, touches nothing live, needs no restart.
