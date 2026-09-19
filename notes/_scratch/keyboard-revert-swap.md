# Keyboard fix — side-by-side test lanes

Date: 2026-09-18 (lanes renamed same day: `oic2`/`fix`/`upstream` -> `cptr-fixes`/`cptr-upstream`)

## Naming

A "lane" = one running build. Each lane has its own port, its own database, and
serves exactly one frontend bundle. Names describe **what the build contains**,
not when it was created:

| Host | Port | Build | Keyboard fix |
|---|---|---|---|
| `cptr.aijly.com` | 4200 | your `main` (`8e539fd`): all local changes | **yes** |
| `cptr-fixes.aijly.com` | 4202 | `upstream/main` + the fix **only** — the PR commit | **yes** |
| `cptr-upstream.aijly.com` | 4201 | `upstream/main` (`f9d1d8c`), unpatched | no |

Why `cptr-fixes` sits in the middle: it's the minimal proof for the PR — upstream
code plus nothing but your patch. If the keyboard behaves there, the fix stands on
its own. If `cptr-fixes` is fine but `cptr` isn't, one of your other local changes
is involved. `cptr-upstream` is the control.

## Verified state

Bundle each lane actually serves (`geometrychange` is the fix's marker):

```
cptr           :4200 -> _app/immutable/nodes/0.CK5WlHHM.js   kb-fix: 1
cptr-fixes     :4202 -> _app/immutable/nodes/0.BHg5zzqw.js   kb-fix: 1
cptr-upstream  :4201 -> _app/immutable/nodes/0.BUI4_8yP.js   kb-fix: 0
```

All three: `/api/health` 200.

Directories (all durable, nothing on `/tmp`):

| Lane | Source dir | Data dir |
|---|---|---|
| `cptr` | `/home/brendan/computer` (branch `main`) | `~/.cptr` |
| `cptr-fixes` | `/home/brendan/cptr-pr` (worktree, detached at `3445131`) | `~/.cptr-fix` |
| `cptr-upstream` | `/home/brendan/cptr-upstream` (full clone, detached at `f9d1d8c`) | `~/.cptr-oic2` |

`3445131` = `f9d1d8c` (upstream/main) + the fix, three files, +67/-36:
`+layout.svelte`, `Terminal.svelte`, `markdown/EditorToolbar.svelte`.

Still on `/tmp`: `/tmp/cptr-pr-kb` holds the `fix/onscreen-keyboard-resize` branch
checkout. Only that worktree is volatile, and the branch ref itself lives in the
main repo, so nothing is lost if `/tmp` goes — just re-add a worktree for it.

## Status / control

```bash
/home/brendan/cptr-lanes.sh                    # table: lane, port, host, health, bundle, fix present
/home/brendan/cptr-lanes.sh restart cptr-fixes # restart one lane
```

## Starting a lane by hand

```bash
cd <source dir>
CPTR_DATA_DIR=/home/brendan/.cptr-<lane> setsid nohup \
  /home/brendan/computer/.venv/bin/python -m cptr.cli run \
  --host 0.0.0.0 --port <420x> --headless >> <source dir>/lane-server.log 2>&1 &
```

Two traps:

- **No `uv` on this box.** Lanes run off `/home/brendan/computer/.venv` with the
  worktree as cwd, so `import cptr` picks up that worktree's source. Safe because
  `pyproject.toml`/`uv.lock` are identical upstream vs. yours.
- **Each lane needs its own data dir.** Upstream's alembic chain stops at `0004`;
  your DB is at `0007`, so sharing one DB fails to start. Fresh DBs migrate
  0001->0004 automatically. `config.toml` is copied from `~/.cptr` into each lane
  dir so providers/keys work.
- Frontend builds symlink `node_modules` to
  `/home/brendan/computer/cptr/frontend/node_modules` to skip `npm ci`.

## Accounts

One login everywhere: `brendan` / `oic2test123` (each lane seeded separately via
`POST /api/auth/setup` with the token printed in its startup log).

Note: the `?token=...` line in a lane's log is the **first-run setup** token. It
stops working once any user exists (`/api/auth/setup` then returns "already set
up"), so it is not a login link — use the username/password.

## Caddy

`C:\caddy\Caddyfile` (= `/mnt/c/caddy/Caddyfile`) is **CRLF** — preserve it, and
match exact bytes when scripting edits. Caddy runs as a Windows service.

```bash
/mnt/c/caddy/caddy.exe validate --config 'C:\caddy\Caddyfile'
/mnt/c/caddy/caddy.exe reload   --config 'C:\caddy\Caddyfile'
```

WSL **cannot** reach Caddy's admin API or its listeners on `localhost` (one-way
WSL networking) — go through Windows interop:

```bash
powershell.exe -NoProfile -Command "Invoke-RestMethod http://localhost:2019/config/"
```

Check the *live* config, not the file (catches a reload that silently didn't
apply). Match host keys with quotes — a bare substring search gives false
positives (`upstream.aijly.com` "matches" inside `cptr-upstream.aijly.com`):

```bash
powershell.exe -NoProfile -Command "\$j = Invoke-RestMethod http://localhost:2019/config/ | ConvertTo-Json -Depth 40; \$j -match '\"cptr-fixes.aijly.com\"'"
```

Verify a host without touching DNS (tests the origin directly, so the cert must
be Caddy's own):

```bash
powershell.exe -NoProfile -Command \
  "curl.exe -s --resolve oic.aijly.com:443:127.0.0.1 https://oic.aijly.com/"
```

Caddy issues Let's Encrypt certs automatically for any name in the config **once
its DNS record exists** — until then the TLS handshake fails outright (HTTP 000,
even with `-k`, because Caddy has no cert to present). That is expected, not
misconfiguration. Note `--resolve` to `:80` is useless for checking which site
block a host matches: Caddy returns 308 to HTTPS for unknown hosts too.

## DNS

`oic.aijly.com` is a plain **proxied A record** to the origin (resolves to
Cloudflare anycast `104.21.36.8` / `172.67.182.174`) — there is **no cloudflared
tunnel** on this box. New lanes are therefore just another proxied A record with
the same target. Public TLS is Cloudflare's; Caddy holds a valid cert of its own
for the name as well (verified via `--resolve`).

Retired names: `oic2.aijly.com` (was an alias for the upstream lane on 4201) was
removed from the Caddyfile; its DNS record can be deleted or left alone.
`oic.aijly.com` and `oi.aijly.com` still point at 4200 and still work.

## Optional 4th lane

"Your main without the fix" would isolate whether your other UI changes affect the
bug: a worktree of `main` with `git revert d6d7787` (`git cherry-pick -R`), built
and served on 4203 as `cptr-nofix`. Not built — ask if useful.

## Still open

- Maintainer wants a written issue report with screenshots/screen recording.
  Not yet captured.
- `open-webui/computer` is `pull_request_creation_policy: collaborators_only`, so
  the PR can't be opened from the `xanax` fork.
