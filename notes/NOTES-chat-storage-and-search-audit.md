# Chat storage & search audit (read-only, no code changes)

Scope: does `search_chats` exist / is it used, do chats ever truly get deleted, and can chat
storage be more efficient. All numbers measured 2026-09-19 against the live `~/.cptr/app.db`
(read-only `sqlite3` URI; no sqlite3 CLI on this box — use `.venv/bin/python`).

## 1. `search_chats`

- Tool: `cptr/utils/tools.py:2491`, registered `approval: "allow"` (2845), category `chats` (3294).
  Args: `query`, `chat_id`, `around_message_id`, `window`, `limit` (max 10), `workspace_scope`
  (`current`/`all`), `include_subagents`.
- Backed by `Chat.search_by_text` (`cptr/models/chats.py:323`): ranked LIKE over
  **chat id / title / summary + `ChatMessage.content`** only (line 350). No FTS, no schema deps.
- Read mode shapes messages via `_shape_chat_tool_message` (`tools.py:2477`):
  `id, role, content, created_at, model?, meta?` — **no tool calls, no tool outputs**.
  Bounded: head 20 + tail 10, middle reachable via `around_message_id`.
- HTTP twin for the UI: `cptr/routers/search.py` (chat + file search).
- Blind spot: tool activity is 56 MB of the 58 MB `chat_messages` table but is invisible to both
  search and read. `search_chats` answers "which chat was that in", not "what did that command
  print". Already used 54× in this history (0.27 MB of results).

## 2. Deletion semantics

- Chats are deleted only on explicit delete: `DELETE /api/chats/{id}` (`cptr/routers/chat.py:914`)
  → deletes the workspace mirror `.cptr/chats/{id}.json` (+ internal children's files) then
  `Chat.delete` (`models/chats.py`), which removes the chat, its messages and internal descendant
  chats.
- Other real message deletes: pending/queued-input coalescing (`chat_task.py:592`), orphaned
  in-progress assistant stub on restart (`utils/timers.py:221`).
- No retention anywhere: no auto-purge, no VACUUM, no size cap. Only retention mechanism is
  UI telemetry and it is manual: `POST /api/perf/prune` → `UiEvent.prune` (`routers/perf.py:136`);
  no automation calls it (automations table has 10 entries, none storage-related).
- `User.delete_user` (`models/users.py`) does not touch chats/messages → orphan rows possible.
- Mirrors are derived, not authoritative: `export_chat_to_file` (`utils/chat_export.py:26`)
  rebuilds `{id}.json` from the DB, `json.dumps(..., indent=2)`.

## 3. Where the bytes are

`~/.cptr/app.db` 63 MB + 15 MB WAL. dbstat: `chat_messages` 58.2 MB, `ui_events` 2.6 MB,
`chats` 0.08 MB.

`chat_messages.output` = 56.1 MB / 32,640 items (only 1.3 MB is `content`). By owner:
reasoning 15.1 MB (34%), `read_file` 9.6 MB, `run_command` 8.9 MB, `agent_tool` 4.2 MB,
`check_task` 3.0 MB, text 1.3 MB; 12,093 tool calls. Caps: `CHAT_TOOL_MAX_CHARS` 50k,
`CHAT_TOOL_COMMAND_MAX_CHARS` 8k — volume comes from call count, not one runaway output.

- Duplication is low overall: per-chat exact dedupe (excluding the regenerated `id`) is
  56.1 → 50.5 MB (10%). gzip -6 of the whole column: 14.5 MB (26%).
- But concentrated: chat `4c61866d` (`agent:grok/grok-4.6`) stores 6.58 MB where ~2 MB is distinct.
  Its assistant messages grow monotonically **132 → 308 → 410 → 447 → 447 items**; 402 of the final
  447 items are byte-identical to the previous message except the fresh item `id`, and the earlier
  message's items reappear in the later one in the same relative order (58/132 found at positions
  0…128). I.e. each new turn re-persists the whole native-agent session transcript → O(N²) per
  agent chat. Ordinary (non-agent) chats show real, unique content: e.g. `6430a34f` 4.7 MB is 95%
  distinct items.
- Outside chats (bigger, and never pruned): `.cptr/cache/audio` **347 MB** across workspaces
  (computer 149 MB / 578 files, aetherion 116 MB, greyhound 54 MB, AIjly 23 MB) with no eviction,
  TTL or size cap (`routers/audio.py`); `.cptr/task_logs` **105 MB** (2,849 files in `computer`
  alone); chat mirrors **54 MB** of pretty-printed JSON duplicating DB content.

## Cheapest wins, in order

1. Audio cache eviction (347 MB, regenerable) — LRU/size budget or mtime TTL.
2. `task_logs` retention (105 MB debug logs) — age or per-workspace count cap.
3. Mirrors: gzip, or write only when DB state changed (~54 MB → single-digit MB).
4. Native-agent transcript dedupe on save (fixes the 4.6 MB chat, prevents 50 MB+ agent chats).
5. Search: extend to `output` items if tool detail should be findable (96% of chat bytes today);
   a compact tool summary in read mode would cover most of it cheaply.
6. Optional: `ui_events` prune on a schedule (2.6 MB, low priority); reasoning items are 34% of
   output and only exist for replay.
