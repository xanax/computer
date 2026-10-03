---
area: workspace-state-ownership
title: Who owns a workspace field — dedicated endpoints vs the layout autosave
aliases: [workspace-prompt, workspace-services, toolServers, put_workspace, autosave, stale-echo, B-019]
updated: 2026-10-03
---

# Who owns a workspace field — dedicated endpoints vs the layout autosave

## Map
- `cptr/frontend/src/lib/stores.ts:864` — `loadWorkspace` builds `currentWorkspace` from
  `GET /api/state/workspace`; `:594 persistWorkspace` + `:636 subscribeForPersistence` debounce ~300 ms and
  PUT the **whole** object back on any store change.
- `cptr/routers/state.py:184 put_workspace` — the layout save. Dedicated writers:
  `/workspace/prompt` (`:280`/`:291`), `/workspace/tool-servers` (`:233`/`:258`), `/workspace/services` (`:334`/`:362`).
- `notes/NOTES-workspace-prompt.md` — the feature and the B-019 write-up; `BUGS.md` B-019.

## Facts
- [verified 2026-10-03 cptr/routers/state.py] `get_workspace` returns the stored data dict **merged with name/path**,
  so the read carries more than the store owns (`prompt`, `services`, `toolServers`). Anything spread from it is echoed
  back by the autosave on the next UI change.
- [verified 2026-10-03 cptr/routers/state.py] `put_workspace` now **drops** `prompt`/`toolServers`/`services` from the
  body unconditionally and re-applies the stored value. Its old guard was `if key not in workspace_data` — an
  *absent-key* guard, which an echo defeats by supplying the key. Never "preserve only if absent" for a field that has
  its own endpoint.
- [ran 2026-10-03 isolated instance :4213] The repro is three calls: `GET /api/state/workspace` (snapshot) → change the
  field via its own endpoint → PUT that snapshot back to the layout endpoint. Pre-fix the edit was lost
  (`MARKER-NEW-EDIT-999` → old text); post-fix prompt and `toolServers` both survive.
- [ran 2026-10-03 isolated instance :4213] `PUT /api/state/workspace` now **ignores** a `prompt` in the body: a test
  that "seeds" a prompt through the layout endpoint reads back `None`. Seed through the dedicated endpoint.
- [ran 2026-10-03 fresh `CPTR_DATA_DIR`] A minted token is not enough — the middleware wants an `auths` row. Call
  `await get_or_create_user(username)` first and sign with the returned id, or every request is `401 unauthorized`.

## Built
- `put_workspace` server-owned keys + `loadWorkspace` deleting `prompt`/`services` from the stored object.
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

## Do not redo
- Re-deriving upstream status: upstream @ `f9d1d8c` (checked 2026-10-03) has no `prompt`/`toolServers` in `state.py`, no
  guard line and no `WorkspaceDashboard` — the whole race is fork-only (B-019).

## Open
- The autosave still writes the full layout ~300 ms after every load (a write on read); see `frontend-reactivity.md`.
- `services` is echoed by other clients the same way; only the layout endpoint's guard covers it today.
