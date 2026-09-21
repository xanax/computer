# Nightly cptr backup → /mnt/e/cptr_backup

**Script:** `~/computer/scripts/cptr-backup.sh` (source of truth) installed as `~/cptr-backup.sh`.
**Schedule:** cptr automation **"Nightly cptr data backup"** (`1f452e17-407a-4e3a-a23c-4f4caeb99da0`,
workspace `~/computer`), `FREQ=DAILY;BYHOUR=3;BYMINUTE=30`. Moved off cron on 2026-09-21 — the user
crontab is now **empty** (`crontab -r`). The automation runs an agent turn that shells out to the
script and then verifies the archive, so every run leaves a chat with the result.
**Log:** `~/.cache/cptr-backup/backup.log` (rotates at 5MB) + a one-line summary per run in
`/mnt/e/cptr_backup/backup.log`.

### Why 03:30 (and what the cptr scheduler does that cron did not)

- **Staggered on purpose:** `aijly-postgres-backup` runs 03:00 (systemd user timer), `imsdb-ingest`
  01:00, `greyhound-archive` 00/06/12/18:07. 03:30 hits a clear slot.
- **Missed runs are late, not skipped.** `Automation.claim_due()` selects every row with
  `next_run_at <= now`, so a due time that passed while the machine/server was down is claimed on the
  first poll after startup (`AUTOMATION_POLL_INTERVAL`, 10s + jitter) and the schedule then advances
  to the *next* future occurrence — i.e. one catch-up run, not N. **Verified empirically 2026-09-21**
  by backdating a probe automation's `next_run_at` by 2h: claimed and dispatched within 8s.
- **The dependency cron did not have:** the cptr server must be running. If it is down at 03:30 the
  backup is simply late. cron.service starts with WSL, so the old entry was independent of cptr.
  There is currently **no** systemd unit / boot hook that starts the cptr server (`_restart_server.sh`
  is manual) — so on a cold boot with no manual start, nothing fires until the server comes up.
- **`automation_runs.status` is not the backup's outcome.** `execute_automation()` records
  `success` the moment the chat's agent turn is *dispatched* (`automation_runs.status = success`
  right after `start_task()`), so the Automations UI shows success even if a later step fails.
  Read the run's chat, or the log, for the real result.

## What is captured

Paths are stored **relative to `$HOME`** so `tar -xzf <archive> -C $HOME` restores in place:

| source | why |
| --- | --- |
| `~/.cptr`, `~/**/.cptr`, `~/.cptr-*` | main data dir + every workspace's agent state (chats, memory, task_logs, skills, uploads) |
| `~/lane-logs` | lane server logs |
| `~/.cache/cptr-chrome` | harness browser profile |
| `~/cptr-lanes.sh`, `~/.cptr-backup-includes` | launcher + this backup's own config |

25 source trees as of 2026-09-21; ~236MB staged → **~77MB gzip** per night, ~20s to build.
Retention: newest 14 archives (`CPTR_BACKUP_KEEP`), SHA256SUMS pruned in step.

## Decisions worth remembering

- **SQLite is snapshotted, not copied.** `~/.cptr/app.db` is 97MB of live WAL database; copying
  files mid-write can capture a torn DB. Each `*.db` is written into the archive via
  `VACUUM INTO` (fallback: the `sqlite3` backup API, then a raw copy *with* its `-wal`/`-shm`),
  then the snapshot is opened and `PRAGMA quick_check`ed. `-wal`/`-shm` never ship on their own.
- **`~/**.cptr/cache` is excluded by default** — `computer/.cptr/cache/audio` alone is 149MB of
  regenerable TTS audio (it would dominate every archive). `CPTR_BACKUP_INCLUDE_CACHE=1` includes it.
- **Source checkouts are not archived.** `~/computer`, `~/AIjly` etc. are git repos with remotes
  (and ~8GB of `.venv`/`node_modules`). Add them via `~/.cptr-backup-includes` if wanted.
- **Archive names carry no `./` prefix.** The first cut used `tar -C stage -czf out .`, so entries
  were `./.cptr/app.db` and the documented single-file restore silently failed; names now come from
  `find -maxdepth 1 -printf '%P\0'`.
- **DB discovery must run against the *source* trees**, not the staged copy: rsync had already
  dropped `*.db` (`--exclude '*.db'`), so a stage-side `find` found zero databases and the archive
  quietly shipped 54MB with no `app.db` at all.
- **`find` + `set -e` silently truncates the source list.** `find ~` exits 1 when it meets an
  unreadable dir; inside a `{ …; printf extras; } | sort -u` block that killed the subshell *before*
  the extras were printed, so `lane-logs`, `~/.cache/cptr-chrome` and `~/cptr-lanes.sh` were dropped
  with no error (the run logged "sources: 21" and happily produced an archive). Every `find` that
  runs under `set -e` now ends in `|| true`.
- **`printf … | grep -q` + `pipefail` = false failure.** The archive-content guard rejected a good
  archive: `grep -q` exits at the first match, `printf` dies of SIGPIPE (141), and `pipefail` turns
  that into a non-zero pipeline. Matching is now pure bash (`case … in *"\n$1\n"*`).
- **The staging area must be pruned from its own scan.** After a crashed run left
  `~/.cache/cptr-backup/stage` behind, the next run found the `.cptr` copies *inside* it — 47 sources
  instead of 25 — and then tried to rsync staging into itself. The work dir is now pruned by
  `-path "$WORK"` and wiped at the start of every run.
- **An archive-content guard sits before publish:** entry count > 100, `MANIFEST.txt` present, and a
  warning if `.cptr/app.db` is absent — so a silently empty/partial archive can never be published.
- **Crash-safe publish:** build in `~/.cache/cptr-backup/out` (never on the small tmpfs, never
  straight onto NTFS), `gzip -t`, size-check after the move to `/mnt/e`, then append the sha256.
  `/mnt/e` absent (WSL without the drive) fails loudly instead of writing a half archive.

## Other scheduled work (audit 2026-09-21)

Everything found running on a timer *outside* cptr, for context on contention and on what the cptr
scheduler now shares this box with:

| scheduler | job | when | what it runs |
| --- | --- | --- | --- |
| systemd user timer | `aijly-postgres-backup` | 03:00 daily | `~/AIjly/scripts/backup_postgres.sh` → `/mnt/e/backups/postgres` |
| systemd user timer | `imsdb-ingest` | 01:00 daily | `~/AIjly/applications/imsdb-scripts/tools/run_nightly.sh` |
| systemd user timer | `imsdb-report` | 09:00 daily | `~/AIjly/.../tools/morning_update.sh` |
| systemd user timer | `greyhound-daily` | 08:45 daily | `~/greyhound-odds/daily_run.sh` |
| systemd user timer | `greyhound-pricewatch` | 09:00 daily | `~/greyhound-odds/price_watch.sh` |
| systemd user timer | `greyhound-archive` | 00/06/12/18:07 + 3min after boot | `archive_results.py --loop --max-hours 288` (long-running) |
| cron (user) | — | — | **empty** (this backup was the only entry; removed) |
| cron (system) | `/etc/crontab` + `/etc/cron.d` | hourly/daily/weekly | Debian defaults only (e2scrub, logrotate, apt, man-db) |
| Windows Task Scheduler | `\Cloudflare-DDNS-aijly` | ~every 5 min | user's own DDNS updater (rest are vendor noise: OneDrive, Zoom, HP, Google, Mozilla) |
| cptr automation | RDP Wrapper offset check | Mon 09:30 | workspace `~/` |
| cptr automation | Fork upstream probe | Mon 09:00 | workspace `~/computer` |
| cptr automation | Sibling fork feature scan | 1st Mon 10:00 | workspace `~/computer` |
| cptr automation | IMSDB ingest / morning update (2) | **paused** | workspace `~/AIjly/applications/imsdb-scripts` — **duplicates the two imsdb systemd timers above**; left paused deliberately |

Night slots in use: 00:07 / 06:07 (greyhound archive), 01:00 (imsdb ingest), 03:00 (postgres backup),
03:30 (this backup). Morning is crowded: 08:45 greyhound-daily, 09:00 greyhound-pricewatch +
imsdb-report + Fork upstream probe. Nothing collides with 03:30.

## Verify / restore

    cd /mnt/e/cptr_backup && sha256sum -c SHA256SUMS          # integrity
    tar -xzf <archive> -C $HOME                               # full restore (stop cptr first)
    tar -xzf <archive> -C $HOME .cptr/app.db                  # one file
    tar -xzf <archive> -O MANIFEST.txt                        # what was in it, cptr git HEAD, sizes

Restore drill done 2026-09-21: extracted `.cptr/app.db` reopened clean — 222 chats,
1024 chat_messages, `quick_check ok`.
