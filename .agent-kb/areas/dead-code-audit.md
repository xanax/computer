# Dead-code audit — the false-positive traps

Area for `scripts/audit-dead-code.py` (`--layer routes|python|tools|frontend`, `--db`).
Full write-up with all six fixes: `notes/NOTES-dead-code-audit.md`.

## Why this matters
The audit found **1 genuinely dead subsystem** and **7 dead Python symbols**, but
only after fixing six bugs that each produced a confident, large, *wrong* answer.
A static audit that over-reports is worse than none: it invites deleting live code.
Fixing them cut routes 264 → 41 and Python symbols 200+ → 7.

## The traps

1. **`_IncludedRouter` hides every route prefix.** This FastAPI version mounts
   routers as objects with no `.path` / `.routes` / `.methods`; `app.routes` reads
   as 11 routes instead of ~230. Children are under `.original_router`, and their
   `.path` **already** carries the prefix — adding `.prefix` double-prefixes.
2. **The frontend never calls raw `fetch`.** It goes through `fetchJSON` /
   `fetchHandler` (`lib/apis/index.ts`). Matching `fetch` alone flags every real
   admin route dead.
3. **Don't anchor the path regex on the call expression.** Two real shapes break it:
   ternary branches (path is not the first arg) and nested template literals
   (the inner backtick ends the literal early). Scan every quoted run starting with
   `/api` or `/v1` instead — over-counting is the safe direction.
4. **Collapsing `${...}` needs balanced braces.** `split("${")[0]` truncates
   `/api/jobs/${id}/cancel` to `/api/jobs/`, so a live route looks dead.
   Both `${id}` and `{job_id}` normalise to `{}` so template == route.
5. **A definition is not a reference.** Counting the `def` line on both sides made
   `send_file` (called 3× by its own router) read as dead.
6. **Decorator-reached code has no call site by construction** — FastAPI handlers,
   pydantic validators, registered tools. Skip decorated defs or the Python layer is
   ~200 rows of guaranteed noise.

## The finding
`/api/automations` (9 endpoints) is fully dead: `routes/automations/*` are 0KB
files that `redirect(308, '/scheduled')`, and automations are rows of the
`/api/jobs` board now. Real dead code from a deliberate rename.

Other zero-caller routes (`/api/browser/frame/*`, `/v1/*`, `/docs`, whatsapp
webhooks, `/api/images/*`) are reached by external callers, not the SPA.

## Judgement calls
- **`.svelte.ts` stores are live** (`gitStatus`, `workspacePriority`); a naive
  resolver reported them unreachable.
- **SvelteKit entry points are not dead**: `app.d.ts`, `+layout.svelte`,
  `+page.svelte`, `+page.ts`, `service-worker.ts`, `lib/index.ts`.
- **A tool at zero historical calls is a usage observation, not dead code**
  (`git_blame`, `send_input`, `timer`, `delete_service`). Snapshot: 3 weeks,
  406 chats, 2,017 messages — good signal, never proof.
- **Do not delete `@tiptap/pm` by inspection** — peer dep of starter-kit.
  `katex` / `iconoir` / `highlight.js` are genuinely unimported.

## Monitoring (built after the audit)
`--json PATH` writes every layer's rows for machine reading; `--baseline PATH`
diffs a run against a stored baseline and **exits 1 only when rows are added**.
Baseline lives at `notes/dead-code-baseline.json`.

**Growth alone is the failure.** Shrinking is the point — that's someone deleting
the automations router, not a regression. Failing on removal would train the
reader to ignore a red result.

**A silent no-op is the whole design.** The weekly automation ("Dead code drift
check", Fri 10:15) reports and notifies *only* on exit 1; a clean week costs the
user nothing. Verified the check by dropping a known row from the baseline and
confirming it came back as `+ resource_path` with exit 1.

Traps hit while building it:
- `import datetime` then `datetime.now()` fails — `now` is an *instance* method on
  the class, not a module function. Use `datetime.datetime.now()`.
- A "doctor the baseline to test it" snippet that does `rows.remove(r)` inside a
  loop with a `break` looks like it worked and doesn't. The first drift test
  passed vacuously; re-filtering with a list comprehension caught it. **A test
  that cannot fail proves nothing** — always doctor the input and watch the
  check go red before trusting a green.
- `--baseline` needs the ids only; `refs`/`where` churn and would create noise.

## Reusable
The audit is a **candidate generator, not an oracle**. Verify each hit with
`grep -rn "\bname\b" --include=*.py .` scoped to `src/` — a bare repo grep
matches `.svelte-kit`/`build` bundles and returns megabytes of minified noise.