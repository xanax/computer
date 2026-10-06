---
area: dev-instance-and-restart
title: The cptr instance this chat runs inside, and what a change needs before it is live
aliases: [restart, reload, --reload, 4200, dev.sh, 9741, prebuilt frontend, frontend/build, npm run build, restart-report.log, cptr-start.log, is my change live]
updated: 2026-10-06
---

# The cptr instance this chat runs inside, and what a change needs before it is live

## Map
- `_restart_server.sh` — the one-shot restarter (reads/writes everything below); its header comment is the spec.
- `cptr-pid.txt` — pid file of the live server; `cptr-start.log` — that server's live log; `restart-report.log` — restart outcomes.
- `dev.sh` — the *other* way to run it: `--reload`, port 9741. Not the instance serving the user.
- `cptr/utils/restart.py` + `POST /api/state/server/restart` — the in-app restart (System Info dialog); see `notes/NOTES-system-info-kill-restart.md`.
- `cptr/app.py:546` — `FRONTEND_BUILD_DIR = cptr/frontend/build`, mounted as static; `notes/`+`.cptr/skills/cptr-frontend/SKILL.md` for the build step.

## Facts
- [verified 2026-10-06 `ps -o args= -p $(cat cptr-pid.txt)`] The live instance is
  `python -m cptr.cli run --host 0.0.0.0 --port 4200 --headless` — **no `--reload`**. A backend edit (any file under
  `cptr/`, including `utils/` and `routers/`) is therefore **not live** until the process is replaced. Editing is not
  shipping.
- [verified 2026-10-06] A frontend edit *is* shipping on its own: `npm run build` writes `cptr/frontend/build`, which the
  running server serves from disk per request, and it serves `index.html` with `Cache-Control: no-cache`, so a page reload
  picks up the new bundle with no restart. Only say "restart" for a frontend change if server code changed too.
- [verified 2026-10-06] …but a *new API route* the new UI calls needs the restart, so a frontend-only-looking change that
  adds an endpoint (e.g. Settings → General's profile card) still needs one before it works end to end.
- [verified 2026-10-06 `_restart_server.sh`] Restart = `bash _restart_server.sh [delay_seconds]` (default 20). It sleeps
  first — the server is the parent of the agent's shell, so killing it kills the live turn — then detaches with `setsid`,
  kills only the pid file's pid plus whatever actually holds `:4200` (`ss -ltnp`), waits for the port to be free, refuses to
  start a second server if it is not, starts `.venv/bin/python -m cptr.cli run … --port 4200 --headless`, health-checks
  `/api/health` for up to 120 s, then mints a token from `~/.cptr/app.db` and smoke-tests the download endpoint.
- [verified 2026-10-06] A stale pid file is a known thing here (the script's own comment: "it held a pid from an earlier
  restart") — trust `:4200`'s listener, not the file, when asking "which process is serving me".
- [verified 2026-10-06] You usually do **not** need a restart to answer "does this route exist": walk `app.routes` through
  `_IncludedRouter.original_router` (see `areas/dead-code-audit.md`); a flat read shows 34 entries and no sub-paths. And a
  `TestClient` cannot discover routes at all — the auth middleware 401s *every* path, real or invented, so 401 proves nothing
  and 404 never happens.

## Decisions
- Never restart the server from a session without being asked: it ends the turn mid-answer, and the user may be watching.
  Do the work, then tell them it needs a restart and how.
- Verify a new endpoint against an isolated instance on a spare port with a fresh `CPTR_DATA_DIR` (mint a token *and* call
  `await get_or_create_user(username)` first — see `areas/workspace-state-ownership.md`), rather than restarting the live one.

## Do not redo
- Reading `dev.sh` and assuming `--reload` is on for the user's instance: it is on for the 9741 lane only. The user's lane is
  4200, `--headless`, no reload.
- Concluding "the change is live because the tests pass" or "because the bundle on disk is new" — the backend needs the
  process replaced, and the two halves of a feature can arrive at different times.
