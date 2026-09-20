# aijly toolsets in cptr

All 12 aijly per-app OpenAPI specs are registered as cptr **external tool servers**,
so their operations are exposed to the model as tool calls. 25 tools total.

They are registered `scope=workspace`, meaning they are **off in every workspace by
default** and get attached per workspace as needed — see "Turning them on" below.

## How they are wired

cptr's external tool servers live in the `tool_servers` config key (an array of
`{id, type, url, path, auth_type, key, ...}`). `cptr/utils/tools.py::_load_tool_servers`
fetches each spec and registers every operation as **`<server-id>_<operationId>`**.

| app id (aijly) | server id (cptr) | tools |
|---|---|---|
| `household-finance` | `household_finances` | household_ingest, household_search, household_snapshot, household_sql, household_todos |
| `abbeyfield` | `abbeyfield` | abbeyfield_property_search, contact_form, graphiti_query, page_search |
| `stags` | `stags` | contact_form, maps_distance, page_search, stags_property_search |
| `smp-scaffolding` | `smp_scaffolding` | contact_form, maps_distance, page_search |
| `1984-analysis` | `orwell_1984` | graphiti_query |
| `wuthering-heights` | `wuthering_heights` | graphiti_query |
| `imsdb-scripts` | `imsdb_scripts` | graphiti_query |
| `knowledge-graph-test` | `knowledge_graph_test` | graphiti_query |
| `dwp` | `dwp` | graphiti_query |
| `pathfinder` | `pathfinder` | pathfinder_search |
| `story-builder` | `story_builder` | build_epub |
| `sandbox` | `sandbox` | query_hardware, web_search |

Spec URL for every server: `http://127.0.0.1:8000/api/applications/<app-id>/openapi.json`
(the `/api/tools/openapi.json?app=<app-id>` form is an alias of the same spec).
Auth: `auth_type=bearer` with the shared `AIJLY_API_KEY`. The spec GET is open;
the key is only enforced on tool-run POSTs.

## Renaming rules (why the ids are not the app ids verbatim)

* Server ids must match `[a-z0-9_]+` — cptr's admin API rejects hyphens, so
  `smp-scaffolding` → `smp_scaffolding`.
* `1984-analysis` → **`orwell_1984`** rather than `1984_analysis`: model APIs that
  accept function names (Gemini in particular) require the name to *start with a
  letter or underscore*, so a digit-leading tool name would be unusable there.

## Turning them on

Since the servers are `scope=workspace`, each one has to be attached to a workspace
before it appears to the model. In the UI: **workspace context menu → tool servers**,
flip the toggles, Save. The modal lists all 12 regardless of scope (the
`/api/state/tool-servers` endpoint deliberately ignores scope) and shows each
server's `description`, which is why those are set to `aijly app: <app-id>` — that
subtitle is what identifies the row in that modal.

By API, same call the modal makes:

    curl -X PUT 'http://127.0.0.1:4200/api/state/workspace/tool-servers?path=/path/to/ws' \
      -b /tmp/verify-cookies.txt -H 'Content-Type: application/json' \
      -d '{"toolServers":["stags","abbeyfield"]}'

Notes on the mechanics:

* A workspace with **no** `toolServers` key has `attached=None`, which means *nothing*
  is attached — absence is not "everything". `server_allowed_in_workspace` returns
  False for any workspace-scoped server in that state, and `toolServers: []` behaves
  the same way, so an untouched workspace has all 12 off.
* They are also unavailable in a **workspace-less "global" chat**: attachment is
  resolved from the workspace path, and with no workspace there is nothing to
  attach to. To use these tools you must be in a workspace *and* have attached them.
* `PUT /api/state/workspace` (the layout/group save) **preserves** `toolServers`
  when the payload omits it, so a running tab saving layout will not clobber the
  attachment list.

Verified: after the switch the `computer` workspace's real tool list is 44 tools
with **0** aijly tools; attaching `sandbox` via that endpoint makes exactly
`sandbox_query_hardware` + `sandbox_web_search` appear, and detaching returns it to 0.

## Re-running / verifying

    .venv/bin/python .cptr/harness/mint-cookie.py        # admin session cookie
    .venv/bin/python scripts/add-aijly-toolsets.py       # idempotent: create/update + verify
    .venv/bin/python scripts/add-aijly-toolsets.py --dry-run
    .venv/bin/python scripts/check-aijly-toolsets.py     # schema + live execution check

The API key is **not** stored in the script: `.env.local`/`.env.dev`/`.env.prod` all
share one value, so it is a prod-capable secret and has no business in git history.
It resolves as `--key` → `$AIJLY_API_KEY` → the key already held by a registered
server in `$CPTR_DATA_DIR/config.toml`. So once one aijly server exists, re-runs need
no key: `AIJLY_API_KEY=... scripts/add-aijly-toolsets.py` only for a fresh instance.

`add-aijly-toolsets.py` talks to the admin API (`POST/PUT /api/admin/tools/servers`,
then `.../verify`), which is what makes a running cptr pick the servers up:
`_save_tool_servers` calls `invalidate_tool_server_cache()`, so the next tool-spec
build re-fetches. **Editing `~/.cptr/config.toml` by hand does not work while the
server runs** — that file is a *mirror* written from the DB (`models/config.py`)
and is only *seeded into* the DB at startup (`utils/db.py`), so a live change has
to go through the API (or be followed by a restart).

`/api/admin/*` needs an admin session cookie. The harness cookie file is written
for host `127.0.0.1`, and curl filters cookies by domain, so
`curl -b /tmp/verify-cookies.txt http://localhost:4200/...` returns
`{"error":"unauthorized"}` while `http://127.0.0.1:4200/...` works. Not an auth bug.

## Notes / gotchas

* **All 12 are `scope=workspace`**, i.e. OFF everywhere until a workspace opts in.
  This is the script's default. The reason is that the per-workspace toggle is
  rendered *"always on" and disabled* for `scope=global` servers
  (`WorkspaceToolServersModal.svelte`: `value={scope === 'global' ? true : ...}`,
  `disabled={scope === 'global' || ...}`), so a global server **cannot be switched
  off from the UI at all** — `workspace` is the only scope where the switch works.
  Use `--scope global` to pin them on everywhere instead.
* **`sandbox` differs by lane**: 2 tools on `:8000` (`query_hardware`, `web_search`),
  **0 on `:3033`**. Since the server URL is pinned to `:8000` the tool set is stable,
  but the same app id is not a portable attachment target across aijly hosts.
* **aijly prod (`:3031`) is stale** — 404 for every `/api/applications/<app>/openapi.json`
  and 405 for tool POSTs, because its baked `core/tool_api.py` predates per-app spec
  mounting. It needs `docker compose ... up -d --build` before any agent can attach to it.
* Only the cptr main lane (`:4200`, data dir `~/.cptr`) got these. The fork lanes
  (`cptr-fixes` :4202, `cptr-upstream` :4201) have their own config stores and were
  deliberately left alone.

Verified 2026-09-20 against `http://127.0.0.1:8000`: all 12 specs 200, all 12 servers
verified through the admin API, 25 tools visible to `_load_tool_servers`, `sandbox_query_hardware`
returned live hardware data end-to-end, `pathfinder_search` reached the app (returned its
own `plan_id is required` validation error, i.e. the bearer key was accepted).
