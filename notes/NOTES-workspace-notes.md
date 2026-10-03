# Workspace notes — "stick a note on this workspace"

A note is one line stuck on a workspace: *the deploy script moved*, *tests/x is
flaky*, *I left the build output in /tmp/xyz*. The point is that it outlives the
chat that produced it and is read by **both** sides:

- the **human** sees it on the workspace dashboard and can add/remove one there;
- the **agent** adds one with `add_workspace_note`, and every chat opened in that
  workspace starts with the notes already in its system prompt.

So a chat that finds something worth remembering writes it down, and the next
chat (and the human's board) reads it without anyone re-discovering it.

## Where it lives

`workspaces.data["notes"]` — a list on the existing JSON column, so **no
migration**. Per user + path, resolved through the same `canonical_path`
normalisation as the rest of the workspace rows (`~/x`, `/tmp/x/` and a symlink
to `/tmp/x` are one row).

- `cptr/models/workspaces.py` — `make_note(text, author)` (trims, raises
  `ValueError` on blank or over `MAX_NOTE_CHARS = 4000`, mints a 12-hex-char id,
  `created_at` ms, author coerced to `NOTE_AUTHORS = ("human", "agent")`),
  `notes_from(row)`, `await get_notes(user_id, path)` (oldest first),
  `await add_note(...)`, `await delete_note(user_id, path, note_id) -> bool`.
  `MAX_NOTES = 200` per workspace.
- `add_note` reads the row, appends, writes the whole `data` dict back: `data` is
  one column, so a partial write would drop the layout beside it.

## The dashboard (human side)

- `cptr/routers/state.py` — `GET /api/state/workspace/notes?path=…`,
  `POST /api/state/workspace/notes?path=…` (body `{text}`), `DELETE
  /api/state/workspace/notes/{note_id}?path=…`. 400 on blank/over-cap, 404 on an
  unknown note id, 403 without a session. Reads never fail: a signed-out or
  unknown workspace answers `{"notes": []}`. **The query param is `path`, not
  `workspace`** — a `workspace=` query is a 422.
- Author from the dashboard is always `human`; the tool writes `agent`;
  `notes_from` maps anything else to `human`.
- `cptr/frontend/src/lib/apis/state.ts` — `getWorkspaceNotes` /
  `saveWorkspaceNote` / `deleteWorkspaceNote` (server response carries the full
  list, so the client never re-derives it).
- `cptr/frontend/src/lib/components/WorkspaceDashboard.svelte` — the notes card
  (`brain` icon), a full-width section directly under the workspace-prompt card
  and above the services card: newest first (`shownNotes`), `you` / `from a
  chat` + a relative time per row, a `trash` icon button to remove one, and a one-line
  composer (input + Add; Enter submits). A local `notesBusy` guards
  double-submits and disables the remove buttons; a failed add shows the error
  line and keeps the draft in the box.
- i18n: `dashboard.notesTitle|notesHint|notesPlaceholder|addNote|notesEmpty|
  notesByYou|notesByAgent` in all 10 locales, via
  `scripts/add-workspace-notes-locales.py` (idempotent, `--check`).

## How the agent gets it

`cptr/utils/prompt_templates.py`:

- `load_workspace_notes(user_id, path)` reads them ("" / [] on any failure).
- `format_workspace_notes(notes)` renders `[WORKSPACE NOTES]` + one intro line +
  `- (agent|human) <text>`, **newest first**, capped at `MAX_NOTES_IN_PROMPT = 20`
  with a trailing `(N older notes not shown.)`, and `MAX_NOTE_CHARS_IN_PROMPT = 500`
  per note (ellipsised at the limit). The intro line names `add_workspace_note`,
  which is how a model that has never seen the tool learns it exists.
- `load_system_prompt` places the block after `{{WORKSPACE_SERVICES}}` /
  `{{WORKSPACE_PROMPT}}` when the template has that anchor, else prepends it —
  the same rule the workspace prompt uses. `{{WORKSPACE_NOTES}}` is available to
  a custom template for explicit placement.
- A chat with **no workspace** (home) gets nothing.

Tools (`cptr/utils/tools.py`, group `notes`): `list_workspace_notes` (re-read the
list, newest first) and `add_workspace_note(text)`. Both apply immediately with
**no approval** — they only touch a list the human can see and delete in one
click. Both are in `GLOBAL_CHAT_DISABLED_TOOLS`: a home chat has no workspace to
pin a note on, so it never sees them. The admin toggle is
`models.builtinTools.notes` / `notesDesc` (en only, like `todos`/`telemetry`).

Writes from either side call `emit_workspace_notes_changed(user_id, path)`
(`cptr/socket/main.py:150`) → the `workspace_notes_changed` event.
`WorkspaceDashboard.svelte:345-356` subscribes to `events:chat` and calls
`loadNotes(workspace)` for it **without reloading the board** (a note landing
mid-turn moves nothing else), so an open dashboard shows the agent's note live.

## Not reverting a note (B-019)

`notes` is a field with its own endpoint sitting inside the object the layout
autosave echoes, exactly like `prompt`. Both halves of the B-019 fix cover it:
`put_workspace` drops `notes` from the body and writes the stored value back, and
`loadWorkspace` deletes `notes` from the store before the autosave can echo it.
`test_a_stale_layout_save_cannot_revert_a_note` is the regression test.

## Verification

- `tests/test_workspace_notes.py` — 27 tests: storage (blank/trim/ids/cap/order,
  author defaulting, unknown path), prompt injection (position, order, cap,
  truncation, explicit `{{WORKSPACE_NOTES}}` placement, no duplication, home chat
  clean), and the tools (agent writes an agent-authored note, tools resolve in a
  workspace chat, **absent** in a home chat, switchable off with the group).
- Browser probes on a throwaway instance (`CPTR_DATA_DIR=/tmp/cptr-notes-verify`,
  port 4299, a **copy** of the real data dir) driven by
  `.cptr/harness/cdp.mjs`: add renders the row on the dashboard and the server
  agrees; delete empties it back to the "No notes yet." plate. The four probe
  scripts are kept in `notes/kb-evidence/probe-notes-{discover,add,reload,delete}.js`
  (they default to `/home/brendan/computer`, so point `WS` elsewhere before
  reusing them on another workspace).
- The LLM half was checked against the live copy with
  `notes/kb-evidence/probe-notes-prompt.py`: `load_system_prompt` for the real
  user + `/home/brendan/computer` renders `[WORKSPACE NOTES]` between
  `[WORKSPACE PROMPT]` and `You are Computer (cptr)` with the note's author tag
  (12 076 chars of prompt).
- No live chat turn was spent on it: the model side is the prompt block plus a
  registered tool, both covered by the tests above.

### Notes for future probes

- To probe against real data without touching it: `sqlite3 app.db ".backup
  /tmp/x/app.db"`-style copy of `~/.cptr` (or `cp -a`), checkpoint the WAL, then
  run the server with `CPTR_DATA_DIR=/tmp/…`. Kill the spare instance when done.
- Mint the cookie for that instance with
  `.venv/bin/python .cptr/harness/mint-cookie.py --data-dir /tmp/…`, and drive
  the UI at `http://127.0.0.1:PORT/?workspace=/home/brendan/computer&view=dashboard`;
  rows take a few seconds to paint, so `--wait` before asserting.
- The live server on :4200 predates this feature — it needs a restart (and the
  rebuilt frontend) before notes appear there.
