# Workspace prompt — "what is this workspace for"

Every workspace can carry a short description/prompt. It is **shown on that
workspace's dashboard** and **sent at the start of every conversation in that
workspace**, so a chat opened there knows where it is before it reads anything
else.

## Where it lives

`workspaces.data["prompt"]` — no schema change. Data is per user+path, and the
lookup (`Workspace.get_prompt`) resolves a path through the same
`canonical_path` normalisation the rest of the workspace rows use, so `~/x`,
`/tmp/x/` and a symlink to `/tmp/x` are the same row.

- `cptr/models/workspaces.py` — `prompt_from(row)` / `await get_prompt(user_id, path)`.
- `cptr/routers/state.py` — `GET`/`PUT /api/state/workspace/prompt?path=…`
  (body trimmed; empty pops the key). Saving tabs PUTs only its own keys, so the
  prompt survives the tab editor — but see *Why an edit reverted* below: the
  client, not the endpoint, is what used to lose that race.
- `cptr/frontend/src/lib/apis/state.ts` — `getWorkspacePrompt` / `saveWorkspacePrompt`.
- `cptr/frontend/src/lib/components/WorkspaceDashboard.svelte` — the card at the
  top of the board: read-only when set, a textarea with Save/Cancel when editing,
  a dashed "No prompt yet." plate when unset. Reads on the same
  `Promise.allSettled` as chats/todos/jobs, so the board still paints in one pass.
- i18n: `dashboard.promptTitle|promptHint|promptEmpty|promptPlaceholder` in all
  10 locales, via `scripts/add-workspace-prompt-locales.py` (idempotent, `--check`).

## Why an edit reverted (B-019)

"I saved the prompt and it didn't save" was neither the endpoint nor the
textarea. `PUT /api/state/workspace/prompt` always stored the text. What undid it
was the layout autosave in the background:

1. opening the dashboard reads `GET /api/state/workspace`, which returns the
   layout **and the prompt as it was**;
2. `loadWorkspace` spread that whole payload into `currentWorkspace`, the prompt
   included;
3. `subscribeForPersistence` debounces ~300 ms and PUTs that object back on every
   change — so the next tab click, pane switch or split re-sent the *pre-edit*
   prompt to `PUT /api/state/workspace`;
4. `put_workspace` preserved `prompt` only when the incoming body **omitted** the
   key (`if key not in workspace_data`), and the echo supplied it.

Same shape for the tool-server attachments: the modal's save was reverted by the
next tab click, because the store still held the old list.

Fixed on both sides, because either half alone leaves a hole:

- `put_workspace` treats `prompt`, `services` and `toolServers` as server-owned —
  they are dropped from the body and the **stored** value is written back, so a
  stale echo (an old tab still running the previous build) cannot win either;
- `loadWorkspace` deletes `prompt` and `services` from the object it puts in the
  store, so the autosave no longer echoes fields the store does not own.

Regression test: `test_a_stale_layout_save_cannot_revert_the_prompt` — it PUTs
`get_workspace`'s own output back after an edit, and fails with
`assert 'Old text.' == 'New text.'` without the router half.

## How it reaches the model

`cptr/utils/prompt_templates.py`:

- `load_workspace_prompt(user_id, path)` reads it (returns "" on any failure).
- `format_workspace_prompt(text)` renders the block `[WORKSPACE PROMPT]\n<text>`.
- `load_system_prompt` places that block. A template that carries its own
  `{{WORKSPACE_PROMPT}}` keeps its own position; otherwise the block is
  prepended, so it leads the prompt ahead of instructions and memory.
- The value is substituted as data by the single-pass `_render_template`, so
  braces in the author's text are literal — a prompt saying `{{FILE_TREE}}`
  prints `{{FILE_TREE}}` and cannot smuggle a slot.

## Verification

- `tests/test_workspace_prompt.py` — 12 tests: storage + trimming, path
  normalisation, injection position, no prompt → untouched, no duplication,
  literal text, unset/blank → empty, round trip through the router, clearing,
  survival of a tabs save, and a **stale** tabs save (the echo the autosave
  really sends) not reverting the prompt or the tool-server attachments.
- Live end-to-end on an isolated instance (`CPTR_DATA_DIR=/tmp/ws-prompt-e2e`,
  port 4213, minted session cookie): `PUT`/`GET` round trip over HTTP; the
  rendered system prompt for a real user+workspace began
  `[WORKSPACE PROMPT]\nA scratch workspace for probing.` exactly once.
- B-019 reproduced and re-verified the same way (isolated instance on :4213,
  temp data dir): `GET /api/state/workspace` → edit the prompt → PUT that very
  snapshot back. Before the fix the snapshot's `Old text.` won; after it,
  `MARKER-NEW-EDIT-999` and `["srv-2"]` both survived the echo. The live server
  on :4200 needs no restart for the *prompt* half once the rebuilt frontend is
  loaded (the client stops sending the key, and the absent-key guard covers it);
  the tool-server half needs the new `put_workspace`.
- Browser probe (`.cptr/harness/probe-workspace-prompt.js`): card renders the
  saved text, Edit pre-fills, Save persists ("Saved" appears, API agrees),
  clearing returns the dashed plate + "Add", restore works.
  `.cptr/harness/probe-workspace-prompt-mono.js` asserts mono purity.
- Screenshots: `notes/_scratch/workspace-prompt-dashboard.png`,
  `-bw.png`, `-bw-dark.png`.

### Notes for future probes

- The theme is stored at `prefs.appearance.theme` (not the legacy top-level
  `prefs.theme`): `curl -XPUT /api/state/preferences -d '{"appearance":{"theme":"bw-dark"}}'`
  then reload; the app writes the legacy key back on boot and would otherwise
  clobber a top-level `theme` you set.
- The JWT secret is generated by whoever calls `_get_jwt_secret()` first. A
  process holding a stale `_config_cache` can overwrite `config.toml` with its
  own secret (that is what made a minted cookie fail with
  `InvalidSignatureError`). Mint **after** the server has written its secret,
  or re-mint if the first request 401s.
- A token alone is not enough on a fresh instance: `create_token` just signs a
  claim, while the middleware wants an `auths` row for that username. On a
  brand-new `CPTR_DATA_DIR`, `await get_or_create_user("e2e")` first (and sign
  with the id it returns), or every request answers
  `401 {"error":"unauthorized"}`.
