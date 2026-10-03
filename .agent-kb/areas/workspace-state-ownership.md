---
area: workspace-state-ownership
title: Who owns a workspace field — dedicated endpoints vs the layout autosave
aliases: [workspace-prompt, workspace-services, workspace-notes, toolServers, put_workspace, autosave, stale-echo, B-019]
updated: 2026-10-03
---

# Who owns a workspace field — dedicated endpoints vs the layout autosave

## Map
- `cptr/frontend/src/lib/stores.ts:864` — `loadWorkspace` builds `currentWorkspace` from
  `GET /api/state/workspace`; `:594 persistWorkspace` + `:636 subscribeForPersistence` debounce ~300 ms and
  PUT the **whole** object back on any store change.
- `cptr/routers/state.py:184 put_workspace` — the layout save. Dedicated writers:
  `/workspace/prompt` (`:280`/`:291`), `/workspace/tool-servers` (`:233`/`:258`), `/workspace/services` (`:334`/`:362`),
  `/workspace/notes` (`:324`/`:334`/`:357` — the same key lives in `workspaces.data`, guarded the same way).
- `notes/NOTES-workspace-prompt.md` — the prompt feature and the B-019 write-up; `notes/NOTES-workspace-notes.md` — notes (human + agent); `BUGS.md` B-019.

## Facts
- [verified 2026-10-03 cptr/routers/state.py] `get_workspace` returns the stored data dict **merged with name/path**,
  so the read carries more than the store owns (`prompt`, `services`, `toolServers`). Anything spread from it is echoed
  back by the autosave on the next UI change.
- [verified 2026-10-03 cptr/routers/state.py] `put_workspace` now **drops** `prompt`/`toolServers`/`services`/`notes`
  from the body unconditionally and re-applies the stored value. Its old guard was `if key not in workspace_data` — an
  *absent-key* guard, which an echo defeats by supplying the key. Never "preserve only if absent" for a field that has
  its own endpoint.
- [ran 2026-10-03 isolated instance :4213] The repro is three calls: `GET /api/state/workspace` (snapshot) → change the
  field via its own endpoint → PUT that snapshot back to the layout endpoint. Pre-fix the edit was lost
  (`MARKER-NEW-EDIT-999` → old text); post-fix prompt and `toolServers` both survive.
- [ran 2026-10-03 isolated instance :4213] `PUT /api/state/workspace` now **ignores** a `prompt` in the body: a test
  that "seeds" a prompt through the layout endpoint reads back `None`. Seed through the dedicated endpoint.
- [ran 2026-10-03 fresh `CPTR_DATA_DIR`] A minted token is not enough — the middleware wants an `auths` row. Call
  `await get_or_create_user(username)` first and sign with the returned id, or every request is `401 unauthorized`.
- [verified 2026-10-03 cptr/routers/state.py, cptr/models/workspaces.py] Notes are the third such field:
  `workspaces.data["notes"]` (no migration), per user + `canonical_path`, `MAX_NOTES = 200`,
  `MAX_NOTE_CHARS = 4000`. `put_workspace` drops `notes` too, and `loadWorkspace` deletes it. The endpoint's query
  param is **`path`** — `?workspace=…` is a 422.
- [verified 2026-10-03 cptr/utils/prompt_templates.py] `load_system_prompt` renders notes as a `[WORKSPACE NOTES]`
  block (newest first, 20 notes / 500 chars each, then `(N older notes not shown.)`) right after the
  `{{WORKSPACE_SERVICES}}`/`{{WORKSPACE_PROMPT}}` anchor, else prepends it; a home chat (`workspace == ""`) gets none.
  The block's intro line names `add_workspace_note`, which is how the tool is discoverable at all.

## Built
- `put_workspace` server-owned keys + `loadWorkspace` deleting `prompt`/`services`/`notes` from the stored object.
- Workspace notes end to end: dashboard card (`WorkspaceDashboard.svelte`) ⇄ `GET|POST /api/state/workspace/notes`
  and `DELETE …/notes/{id}` ⇄ `workspaces.data["notes"]` ⇄ the `[WORKSPACE NOTES]` prompt block ⇄ the `notes` tool
  group (`list_workspace_notes`, `add_workspace_note`, `approval: allow`, in `GLOBAL_CHAT_DISABLED_TOOLS` because a
  home chat has no workspace). Either side's write emits `workspace_notes_changed`; `WorkspaceDashboard.svelte:345`
  reloads just the notes card for it.
- `tests/test_workspace_notes.py` (27 tests) covers storage, prompt injection and the tools; the live-prompt render was
  checked with `notes/kb-evidence/probe-notes-prompt.py`.
- `tests/test_workspace_prompt.py::test_a_stale_layout_save_cannot_revert_the_prompt` with a `_json_request()` helper
  (a starlette `Request` carrying a real JSON body, which the router tests otherwise never do).

## Decisions
- Fix both layers, not just the client: a tab still on the old build, or any other client that echoes, would keep
  clobbering. Rejected: making the client re-read the workspace after a prompt save — hides the race, does not close it.
- Keep `toolServers` in the store type (the client still sends it; the server ignores it) rather than widening this fix.

## Dead ends
- Blaming the textarea, the Save handler or the prompt endpoint: all fine — `GET /workspace/prompt` immediately after
  the save shows the new text. The revert happens *later*, after an unrelated UI action, which is what makes it look
  like "it didn't save".
- Looking for the admin UI edit for a new builtin-tool group: `Admin/Models.svelte` renders `BUILTIN_TOOL_GROUPS`, so
  adding the group to `tools.py` plus its two locale keys is the whole change (`notes` was already listed at `:83`).
- Expecting the new group's label in every locale: `models.builtinTools.notes`/`notesDesc` are in `en.json` only —
  exactly like `todos` and `telemetry`, which predate the feature, so the admin page falls back to English for those
  three groups. Not a regression, and not worth inventing nine translations for.

## Do not redo
- Re-deriving upstream status: upstream @ `f9d1d8c` (checked 2026-10-03) has no `prompt`/`toolServers` in `state.py`, no
  guard line and no `WorkspaceDashboard` — the whole race is fork-only (B-019).

## Open
- The autosave still writes the full layout ~300 ms after every load (a write on read); see `frontend-reactivity.md`.
- `services` is echoed by other clients the same way; only the layout endpoint's guard covers it today.
