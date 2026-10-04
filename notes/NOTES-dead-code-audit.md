# Dead-code audit — method and the traps that produced 200+ false positives

`scripts/audit-dead-code.py` (run with `--layer routes|python|tools|frontend`, `--db`).
Four axes: FastAPI routes vs frontend calls, Python symbols vs repo references,
tools vs `chat_messages` history, frontend files vs importers.

The hard part was not finding dead code, it was **stopping the audit lying**.
Each of these produced a confident, wrong, very large result until fixed.

## 1. FastAPI route flattening lost every prefix
This FastAPI version mounts each router as an `_IncludedRouter`: no `.path`, no
`.routes`, no `.methods`. A flat read of `app.routes` sees **11 routes instead of
~230**, so the audit reported `/api/git` etc. as called by nothing.
The children live behind `.original_router`, and *their* `.path` already carries
the full prefixed path (`/api/admin/users`). So recurse into `.original_router`
and **do not** add its `.prefix` — that double-prefixes to `/api/admin/api/admin/users`.

## 2. Matching only `fetch` hid the whole frontend API layer
The frontend calls the backend through `fetchJSON` / `fetchHandler`
(`lib/apis/index.ts`), never raw `fetch`. Matching `fetch` alone flagged every
real admin route as dead (264 rows).

## 3. Regex anchored on the call fails on the two shapes that actually occur
- **Ternary branch**: `` ws ? `/api/skills?x=` : '/api/skills' `` — path is not
  the first argument.
- **Nested template literal**: `` `/api/ui-events/summary${qs ? `?${qs}` : ''}` ``
  — the inner backtick ends the literal early.
Fix: don't anchor to a call expression at all. Scan **every** quoted run that
*starts* with `/api` or `/v1`. This over-counts, which is the safe direction —
a route wrongly claimed alive costs nothing; nothing live is called dead.

## 4. Collapsing `${...}` needs balanced braces
`path.split("${")[0]` truncates `/api/jobs/${id}/cancel` to `/api/jobs/`, which
then matches no route and makes a live endpoint look dead (reintroduced 45 rows
that the previous bug had hidden). Count braces instead, so nesting and an
interpolated backtick both survive. `normalise()` then maps both `${id}` and
`{job_id}` to `{}`, so the template and the route compare equal.

## 5. Counting a definition as its own reference
`corpus[name] <= refs_here` counts the `def` line in both sides, so
`send_file` — called three times by its own router — read as dead. A definition
is not a reference: subtract it.

## 6. Decorator-reached code has no call site by construction
FastAPI handlers, pydantic validators and registered tools never appear as call
targets. Skip any def carrying a decorator, or the Python layer is ~200 rows of
guaranteed noise (`write_memory`, `truncate`, `scheduler_worker_loop`, ...).

Net effect of fixing all six: python 200+ → **7**, routes 264 → **41**.

## Verifying: the audit is a candidate generator, not an oracle
Spot-check with `grep -rn "\bname\b" --include=*.py .` before deleting anything.
Scope greps to `src/` — a bare repo grep matches `.svelte-kit`/`build` bundles
and returns megabytes of minified noise.

## Findings

**`/api/automations` is entirely dead (9 endpoints)** — the strongest result.
`routes/automations/` is four 0KB files that `redirect(308, '/scheduled')`.
Automations are rows of the `/api/jobs` board now (`NOTES-unify-tasks.md`), and
no frontend file names the path. Real dead code from a deliberate rename.

Other zero-caller routes are the proxy/browser internals (`/api/browser/frame/…`,
`/api/browser/resources/…`), `/v1/*` + `/docs` + `/openapi.json` (OpenAI-compatible
gateway, not frontend-called), `/api/webhooks/whatsapp/*` and `/api/images/*`
(whatsapp bridge) — all reachable by an external caller, not the SPA.

**7 Python symbols** referenced nowhere (each appears only on its own `def` line):
`ChatCompletionMessage`, `active_count`, `get_event_catalog`, `get_user_uid_gid`,
`list_async_subagents`, `open_in_system_browser`, `resource_path`.

**Frontend files with no importer.** Most are SvelteKit conventions, not dead code —
`app.d.ts`, `routes/+layout.svelte`, `routes/+page.svelte`, `+page.ts` loaders,
`service-worker.ts`, `lib/index.ts` (the `$lib` alias comment) are all
framework entry points. Genuinely dead: `lib/apis/uploads.ts` (now a
`// Moved to apis/auth.ts` stub), `Bar.svelte`, `PortNotification.svelte`,
`QuickOpen.svelte`, `chat/AttachButton.svelte`, and the four 0KB `routes/automations/*`.

**.svelte.ts stores are live** — the module resolver initially reported
`gitStatus` / `workspacePriority` as unreachable. They are `.svelte.ts` runes
modules; importing them is the point.

**npm runtime-unused:** `katex`, `iconoir` (paths are inlined in `Icon.svelte`,
never imported), `@tiptap/extension-text-align`, `@tiptap/suggestion`,
`@tiptap/pm`, `highlight.js` (only named in `app.css` comments),
`@sveltejs/adapter-auto`. The rest (`vite`, `prettier`, `svelte-check`,
`@tailwindcss/vite`, `adapter-static`) are build/config-time — not dead.
Do not delete `@tiptap/pm` by inspection; it is a peer dep of the starter-kit.

**Tools never called in history** (3 weeks, 406 chats, 2,017 messages — good signal
but *not* proof of dead: `git_blame`, `send_input`, `delegate_task`, `timer`,
`delete_service`, `toggle_automation`, `reopen_workspace_todo`, `image_generate`).
Several are capabilities, not code: `send_input`/`check_task` exist for long
background jobs, and `git_blame`/`delete_service` are plainly usable. A tool at
zero calls is a *usage* observation, never a deletion.