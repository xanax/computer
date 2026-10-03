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
- `scripts/backfill-chat-index.py` — the backfill (dry run by default).
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

## Decisions
- **Two omissions in the composed form, both deliberate: no English filler labels and no dates.** `summary` is
  *substring*-matched by the ranker, so a literal `Files:` or a year present in all 378 rows would make every chat
  match the query "files" or "2026" and flood results. A distinctive token (`messageParser`, `mono-codemirror`) does
  now resolve to its chat, verified against the DB. Keep this property if the format is ever changed.
- No LLM calls anywhere in the backfill. A checkpoint's prose is reused verbatim; everything else is extracted.
- Precedence: checkpoint prose → else opening question + paths + tool counts.
- A checkpoint describes only the messages dropped **before** it, so the paths touched **after** it are appended as a
  second line. Cheaper and better than the plan's fall-through-when-later-activity heuristic.
- Write the chat's **existing** `updated_at`, never `now` (the `update_summary` default of `0` means "leave it").
- Titles are *not* copied into the composed entry — the ranker already scores title separately.

## Dead ends
- Looking for a new table, FTS index or export file. All of it already exists: one field, already ranked.
- Treating the 9 chats with no `workspace` in `meta` as a bug — paths simply arrive absolute and get dropped; those
  chats still get a question and a tool line.
- "Fixing" the short entries: 9 chats whose entire content is "hi" produce `summary == title`. That is an honest
  index of that chat, not a defect. Do not add a special case.

## Open
- **Phase 2 not built**: `POST /api/chat/{id}/index` + the frontend button. Needs a server restart (no router change
  is live until then) and `npm run build` for the locale/UI change. Restart is the user's call.
- Falls back to the free entry when no utility model is configured, so pressing the button never fails hard.
- Forks inherit the index: `chat.py:1160` copies `summary` on fork, so a fork shows its parent's entry until
  something refreshes it.
- Searching now ranks differently — this is the one behaviour change to an existing feature. `_extract_snippet` can
  return summary text as the snippet.
- No portability: the DB is outside every repo and not versioned; `scripts/cptr-backup.sh` captures `app.db`
  WAL-safe nightly, so it survives disk loss but does not travel with a clone.

## Do not redo
- Re-measuring "is `chats.summary` empty" or "where is it read/ranked" — the map above is current as of 2026-10-03.
- Looking for a `sqlite3` CLI on this box: there is none. Use `.venv/bin/python` with the `sqlite3` module.
