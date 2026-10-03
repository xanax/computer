# Nightly *home* backup (`~/home-backup.sh`)

Whole-`$HOME` tar snapshots, separate from the cptr-data backup.

| | |
|---|---|
| script | `~/home-backup.sh` (source of truth `~/computer/scripts/home-backup.sh` — `cp` it over after every edit) |
| dest | `/mnt/e/home_backup` (E:, 165 GB free) |
| keep | 7 archives (`HOME_BACKUP_KEEP`) |
| schedule | cptr automation "Nightly home backup", 04:15 daily (cptr data backup is 03:30) |
| ignore file | `~/.backupignore` (2 active rules + 44 built-in defaults, all of them listed there) |
| work dir | `~/.cache/home-backup` (`stage/`, scan lists, `report.txt`, `run.lock`, `current.log`, `backup.log`) |
| compressor | `xz -T0 -6` (`HOME_BACKUP_COMPRESS=auto`; same wall clock as gzip, ~40% smaller) |
| size | stage **14.8 GB** (235.7k non-dir entries, 37 live SQLite dbs) → **4.98 GB** as `.tar.gz`, **3.3 GB** as `.tar.xz` |

## Shape of a run

1. **scan** — `/usr/bin/python3` walks `$HOME`, applies `~/.backupignore` + built-in
   defaults, classifies live SQLite databases, writes `include.list` / `materialise.list`,
   prints the report (`report.txt` → `.backup-info/EXCLUSIONS.txt` in the archive).
2. **stage** — `rsync -lptgo --from0 --files-from=include.list` into `$WORK/stage`
   (~15.4 GB / 233k entries, ~5 min on this box).
3. **materialise** — snapshot the live SQLite dbs into the stage, copy their sidecars,
   then verify the stage has every non-dir entry it was asked for (refuses to publish
   if it is materially short).
4. **provenance** — `.backup-info/{EXCLUSIONS,REPOS,TOOLS,IGNORE-RULES,INCLUDED-FILES}.txt`.
5. **containers** — `docker_phase capture` (see below) writes into `.backup-info/`.
6. **compress + verify + publish** — `xz -T0 -6` the stage, `xz -t` it, `tar -tf` it and diff
   that against the staged file list, rename into place, then write `.sha256` `SHA256SUMS`
   `backup.log` `RESTORE.md` and prune beyond KEEP.

## Compressor: `xz -T0 -6`, decided by measurement

There is no `zstd` and no `pigz` on this box, so `HOME_BACKUP_COMPRESS=auto` used to fall back
to **single-threaded `gzip -6`** — 11 of 12 cores idle for ~15 minutes every night. Benchmark
on a 687 MB slice of a real stage (460 MB of the live `atr.sqlite` plus staged source trees,
`nice -19`, 12 cores):

| variant | time | size | ratio |
|---|---|---|---|
| `tar` → `gzip -6` | 36.4 s | 150.7 MB | 0.219 |
| `tar -b20` → `gzip -6` | 36.6 s | 150.7 MB | 0.219 |
| `tar -b1024` → `xz -T0 -1` | **10.6 s** | 109.7 MB | 0.160 |
| `tar -b1024` → `xz -T0 -6` | 44.3 s | **91.5 MB** | 0.133 |

- tar's `-b` block size is noise (1%) — not worth tuning.
- `xz -T0 -6` costs about what `gzip -6` costs (it parallelises, so it uses the whole box) and
  writes **39% less**: ~2 GB less per night, ~14 GB less across the 7 kept archives.
- Decompression is not the feared trade-off: LZMA2 stores incompressible data **raw**, and this
  stage is >40% incompressible SQLite snapshot, so decompression runs at ~850 MB/s — a full
  `tar -tf` verify of an xz archive is as quick as a gzip one (measured: 687 MB in 0.8 s).
  `xz -t` and `xz -T0 -t` measured the same, so the verify keeps the plain form.
- `tar -tf` **auto-detects** `.tar.xz` from the magic, so the verify pipeline needed no change;
  only the `xz -t` branch and the human restore lines (`tar -xzf` → `tar -xf`, GNU detects
  both) did.
- `HOME_BACKUP_COMPRESS=xz1` stays available: 3.5x faster than gzip, 27% smaller.

`EXT` follows the compressor (`tar.xz`) and the retention glob lists both extensions, so old
`.tar.gz` and new `.tar.xz` archives coexist while the changeover beds in.

Measured on the first complete xz run (2026-09-24): **3.3 GB, 42 minutes** wall clock
(08:44 → 09:26) for the same 14.8 GB stage that gzip wrote 4.98 GB from; the on-machine checks
all passed — `stage check: expected 235663 non-dir entries, found 235663`,
`sqlite snapshots: 51 ok, 0 degraded` (the line as it read then — see "Two counts" below; it now
prints `37 ok, 0 degraded (+ 14 live -wal/-shm sidecar file(s))`), `verified: 238166 entries,
staged tree == archive`, `run ended: exit=0`. Compression alone was ~20 min (xz at 1080% CPU),
staging ~8 min, materialise ~11 min (142 s of it the `atr.sqlite` VACUUM INTO plus its
`quick_check`), and the final cross-filesystem publish of 3.3 GB to `/mnt/e` another couple of
minutes.

### Two counts that used to disagree, and now don't

The 08:44 xz run reported **37 databases** in its report
(`sqlite : 37 live database(s) snapshotted via VACUUM INTO / backup API (10 -wal/-shm folded in,
not copied)`) but **51 ok** in the materialise pass's own summary. Both were right — the summary
counted *operations*: 37 database snapshots plus one staged copy per live `-wal`/`-shm` sidecar
(`ok = db_ok + raw_ok`). It is the sidecar count that wobbles between runs, because it depends on
which databases happen to have a live WAL at that moment. Reconstructed from `backup.log`:

| run (2026-09-24) | snapshots | sidecar copies | printed |
|---|---|---|---|
| 07:04 (died later, exit=1 — the dbdumps bug) | 37 | 12 | `49 ok, 0 degraded` |
| 08:09 gzip publish | 37 | 10 | `47 ok, 0 degraded` |
| 08:26 gzip publish | 37 | 10 | `47 ok, 0 degraded` |
| 08:44 → 09:26 xz publish | 37 | 14 | `51 ok, 0 degraded` |

So the headline moved 47 → 49 → 51 across runs in which the database count never changed, and the
one number that *was* stable (37) was the one the summary did not print. The line now prints the
split, databases first: `sqlite snapshots: 37 ok, 0 degraded (+ 14 live -wal/-shm sidecar
file(s))`.

Sidecars are staged *before* the snapshot (`raw` sorts before `sqlite`) and deleted again when the
snapshot is clean, so they are not in the archive; they ship only if a database fell back to a raw
copy, which the `stage check` line reports explicitly ("N sqlite sidecar(s) for raw copies"). The
report's own "(10 -wal/-shm …)" is the *scan-time* list (`materialise.list` carries 10 `raw`
entries), so it can legitimately be lower than the run's sidecar count — 4 more WALs existed by
the time the snapshot pass ran, which is also why "folded in, not copied" is the report's intent
rather than a guarantee. `db_ok`/`raw_ok` keep the two counts apart; the `ok`/`warn` totals still
drive the "refusing to publish" check.

The 04:15 automation's own prompt quotes these numbers as well (it is the `jobs` row
`e102bc46-f978-4f05-a8c4-76887997fbe8`, editable in the Tasks UI, and it is what tells the
nightly agent how to read the log), so it was updated in the same change — a count that lives in
two places is a count that will disagree.

## The first deploy's two one-liners (both silent, one fatal)

The first full run died 19 minutes in with `FATAL: tar failed`:

- **`tar` was invoked without `-c`.** The compressed branch read
  `tar --use-compress-program="$TARCOMP" -f "$TMP_OUT"`, so instead of *creating* the archive
  tar tried to **read** `$TMP_OUT` — which did not exist yet. One missing character.
- **The low-space warning had no closing `]`:** `if [ "$(df -Pk "$WORK" …)" -lt $(( … )) then`.
  bash printed `line 1235: [: missing ']'` and the branch was simply **skipped** — under
  `set -e` a failing `if` *condition* is exempt, which is why the run sailed past it. Benign
  here, but the one warning whose whole job is to catch a full work dir never fired. Now
  closed and proven under real `set -e` (the standalone repro printed `[: missing ']'`).

Lesson: after adding a branch to a `set -euo pipefail` script, run it once for real — both bugs
were invisible to `--dry-run`, to `bash -n`, and to a standalone harness that only set `set -u`.

## Live SQLite: VACUUM INTO, *not* the backup API

`greyhound-odds/data/atr.sqlite` is ~7 GB and a scraper writes to it continuously.
The `sqlite3` **backup API restarts from page 1 every time the source is written**, so
on that database it never commits: the first real run sat 15 min in a restart loop
(`ps` state `D`, CPU time ~0, `/proc/<pid>/io` `read_bytes` climbing at ~100 MB/s while
`write_bytes` and the destination mtime never moved — it had already read the whole db
several times over).

`VACUUM INTO` holds one consistent read transaction, which a WAL writer cannot
invalidate, so it always makes progress. Measured against the live database:

    168 s  →  6.57 GiB snapshot (source 6.67 GiB, writer active: wal 12.7 → 14.8 MB)
    PRAGMA quick_check: ok        tables: 8

Order of attempts is now `VACUUM INTO` → `backup API (pages=4000)` → raw
`db + -wal + -shm` copy, and each is bounded by **`HOME_BACKUP_DB_BUDGET`** (default
1800 s) through `Connection.set_progress_handler`, so one wedged database can no longer
hang the night. `quick_check` on the 6.5 GiB snapshot costs ~7 min; that check plus the
snapshot is where most of a run now goes.

## The stage-completeness check (fixed after a false failure)

The guard that decides "did everything we asked for actually land in the stage?"
compared `os.walk(...).files` against `include.list` + `materialise.list` as one
number. On a real run that read `expected 234212, found 231568 (short by 2644)` and
**refused to publish a perfectly good backup**. None of the 2644 were missing files:

- **2622 directories** — `include.list` carries dirs because `rsync --files-from`
  needs them to build the tree; `os.walk`'s `files` never counts them.
- **49 `materialise.list` entries** — that file is `kind\trelpath` (`sqlite\t…`,
  `raw\t…`), so its paths never matched the staged names.
- **10 symlinks pointing at directories** — they land in the walk's *dirs* list.

The rewritten check builds `want_srcs = {rel: kind}` from both lists, skips source
directories (`os.path.isdir and not islink`), tests `os.path.lexists` per remaining
entry (so symlinks count as themselves), and treats **`raw` entries as optional**:
a `-wal`/`-shm` only ships when its database fell back to a raw copy, since a clean
snapshot deletes them on purpose. Result on that same stage: `expected 231578 non-dir
entries, found 231578`. It also names the first 15 genuinely missing paths, which the
old version never did.

Lesson: a completeness guard must count the same population it is guarding, and its
failure output has to name the offenders — otherwise a false failure is indistinguishable
from a real one and it silently stops the backups.

## `set -e` + a function's last statement (it killed two runs)

The script runs under `set -euo pipefail`. `docker_phase` ended with a `while` loop whose
body finished on `[ -n "$e" ] && printf '    mount  %s\n' "$e"` — and a `while` loop's
status is the status of the **last command in its final iteration**. When the last
container had no mounts, that printed nothing, returned 1, and the failure propagated:
loop → `{ … } > DOCKER.txt` → function → `docker_phase capture` → `set -e` aborted the
script. Both full runs died there **silently**, one second after logging the docker
summary; `.backup-info/INCLUDED-FILES.txt` was never created.

Two lessons, both now baked in:

- **`[ test ] && cmd` as the last statement of a loop or function is a landmine under
  `set -e`.** Use `[ -z … ] || cmd`, or an explicit `if`. Audited the whole script for it;
  the other two instances (the `SHA256SUMS` rewrite and the retention `rm && log`) are fixed.
- **A function that promises "nothing here can abort a backup" must `return 0`.** Added to
  the end of `docker_phase`.
- The `run ended: exit=N` trap is what made this findable: without it the log just stopped
  and looked like a killed process. First time it printed `run ended: exit=1`, which is how
  I knew to stop blaming the harness. Do **not** test a `set -e` script's functions from a
  driver that only sets `set -u` — that is exactly why the first standalone test passed.

## Launching it: detach, or it gets reaped

A run started from a cptr tool call (`nohup … &` inside a `run_command`) **died silently
19 minutes in**, mid-flow, with no `die` line and `INCLUDED-FILES.txt` never created —
the process was killed externally, not by the script. Always launch it in its own
session:

    setsid nohup /home/brendan/home-backup.sh </dev/null >/dev/null 2>&1 &

stdout is no longer where the log lives: `log()` writes `$WORK/current.log` itself, so
redirecting stdout into that file would print every line twice. Watch the run with
`tail -f ~/.cache/home-backup/current.log` (or `backup.log` for history).

The script also ends every run with a `run ended: exit=N` line (an `EXIT` trap), so
**a log whose last line is not `run ended` means the process was killed** rather than
having finished. `flock` on `run.lock` means a re-launch while one is in flight is a
no-op, so a timer and a late manual run cannot collide.

## Container data (not under `$HOME`)

`/var/lib/docker` is root-only and named volumes live outside `$HOME`, so a `$HOME` tar
misses them. `docker_phase` runs inside the archive instead:

**Bug found on the first full run:** the volume branch created its output dir
(`mkdir -p "$INFO/docker"`) but the **db-dump branch never created `$INFO/dbdumps`**, so
`pg_dumpall … | gzip > "$out"` and `docker cp … "$out"` both died with
`No such file or directory`. It did not crash the run — it silently degraded to a **raw
tar of each live database volume, taken while the database was running**. An earlier
standalone test of `docker_phase` had passed only because a `dbdumps/` dir happened to
exist from hand-testing. Fixed with `[ "$plan" = capture ] && mkdir -p "$INFO/dbdumps"`
before the loop, plus a NOTE line in `DOCKER.txt` whenever `CTR_FAILS > 0` explaining
that a failed dump means a possibly-inconsistent raw volume tar.

Verified by extracting the real function into a driver and capturing into a scratch dir
(`HOME_BACKUP_VOLUMES=none`): 4 dumps, all good —

    aijly-postgres-prod.sql.gz    44.8 M
    aijly-postgres-dev.sql.gz    170.2 M      (dev is the big one — not prod)
    aijly-falkordb-dev-dump.rdb   21.7 M
    aijly-falkordb-prod-dump.rdb   5.3 M      = 241.9 M total

so budget ~240 MB of dumps, not the ~45 MB an earlier partial test suggested.

- `pg_dumpall` of every running Postgres container (over the socket, no password;
  `-u postgres` is the OS user inside the image) → `.backup-info/dbdumps/*.sql.gz`
- `redis-cli BGSAVE` + `docker cp` for Redis/FalkorDB (the falkordb volumes are *just*
  `dump.rdb`, so the snapshot replaces the volume capture entirely) → `*-dump.rdb`
- every other named volume, tarred with an `alpine` helper,
  `--exclude='./cache' --exclude='./caches'` (open-webui: 967 MB → 3.0 MB)
- skipped by name as re-downloadable: `*cache*`, `*-m2`, `*trivy*` (`dil-trivy-cache` +
  `trivy-cache` are 2.7 GB of the lot)

Volumes owned by a running db container whose dump succeeded are skipped as
"inside dbdumps/… (logical dump is smaller)". A volume whose dump *fails* is captured
raw instead, so the data is never lost. Per-run summary line: `containers : …`.

The live clusters `~/AIjly/data/postgres-{prod,dev}` are `drwx------ 70:70` stale
4 KB leftovers — unreadable and not the real data (the real data is in the volumes).

## Facts worth keeping

- `python3` in this workspace's PATH is the *workspace venv*; the script uses
  `/usr/bin/python3` (3.14.4) explicitly (`HOME_BACKUP_PYTHON`).
- `.venv` trees alone are **15.2 GB of the 23.7 GB excluded** — the built-in defaults
  carry the weight; `~/.backupignore` needs almost nothing.
- No `zstd`, no `pigz` on this box — but `xz -T0` *is* installed, which makes that irrelevant
  (see the compressor section). Nothing left to install.
- Sizes to expect — **the original 14.8 GB / 3.3 GB figures are stale; do not read the drift as a fault.**
  Measured on 2026-10-03: stage **19.1 GB / 370k non-dir entries / 375k archive entries /
  56 live SQLite dbs (0 degraded, +8 sidecars) → 4.84 GB (4.5 GiB) `.tar.xz`, 42:50 wall clock**.
  Growth is monotonic and tracks one file: `greyhound-odds/data/atr.sqlite` is now **9.0 GB**
  (7 GB at design time), and is incompressible SQLite, so raw and archive rise together.
  Nightly trend from `/mnt/e/home_backup/backup.log`: 3.3G/14.8G raw (09-24) → 3.9G/16.7G (09-26)
  → 4.4G/18.8G, 52 dbs (10-02) → 4.6G/19.1G, 56 dbs (10-03). Up to ~4 GB of archive is therefore
  now **normal**; only a jump outside the raw-size trend means something changed. Space is not a
  worry yet: 7 archives ≈ 32 GB, 173 GB free on `/mnt/e`.
  The generated `~/.cache/home-backup/report.txt` is the authoritative per-run inventory
  (included/excluded, per-db sizes, `N ok / M degraded`) — read it rather than trusting these
  numbers.
  `/mnt/e` reads run at only ~10 MB/s, so the cross-filesystem publish copy of a 3-5 GB archive
  takes minutes and a full `sha256sum -c` takes 8+ minutes — the script proves the archive
  against the staged tree *before* publishing, so the nightly check can stay cheap.
- `~/.backupignore` now carries exactly two active rules, both vendor *installations* rather
  than data: `/opt/calibre/` (621 MB, the program dir — config is `~/.config/calibre`) and
  `/.local/playwright-deps/` (177 MB, unpacked by `playwright install-deps`). Together they took
  the stage from 15.5 GB to 14.8 GB. `.grok/downloads/` (471 MB of CLI installer cache) is
  deliberately left **in**: `~/.local/bin/grok` and `~/.grok/bin/*` are symlinks into it, so
  excluding it leaves a dangling `grok` after a restore.
- Logging: the script writes every line three ways — stdout, `$WORK/backup.log` (history,
  rotated at 5 MB) and `$WORK/current.log` (**this run only**, truncated under the lock at start).
  `current.log` used to be an accident of how the script was launched (the launch recipe
  redirected stdout into it), so a run started any other way — as the nightly automation does —
  left a stale file that looked like a hung backup. Now the script owns it, and any run can be
  watched with `tail -f ~/.cache/home-backup/current.log`; `run ended: exit=N` is the marker of
  a run that got all the way to the end.
- Ignore-file syntax trap: an active rule must start in **column 0** and carry **no trailing
  comment**, because the whole line (post-`rstrip`) becomes the pattern. Explain it on the line above.
- `greyhound-odds` is 11.0 GB of the 14.8 GB stage — 71%, and it is the data this backup exists
  for. `.venv/` (15.2 GB), `.cache/` (5.3 GB), `.vscode-server/` (1.3 GB) and `.git/` (633 MB)
  are the four built-in rules doing all the real work; the biggest included items after
  `greyhound-odds` are `AIjly` 1.5 GB and `.grok` 692 MB.
- The symlink report resolves targets **relative to the link's directory**; an earlier
  version used `os.path.abspath(target)`, which reported `xview/.../strings -> ../../libxview/xstrings`
  as pointing outside `$HOME`.
- `/tmp` is a **7.8 GB tmpfs** and was 100% full — 4.6 GB of it is a stray
  `/tmp/guard.sqlite`. Anything writing a multi-GB scratch file (dumps, restores) must
  use `/var/tmp` or `$HOME`, not `/tmp`. `RESTORE.md` says so.

## Restore drill (run for real 2026-09-24, against the 4.98 GB archive)

    mkdir -p /var/tmp/hb && tar -xf /mnt/e/home_backup/home-backup-<stamp>.tar[.gz|.xz] -C /var/tmp/hb
    tar -tf /mnt/e/home_backup/home-backup-<stamp>.tar.xz | head   # shape check (auto-detects xz)
    cd /mnt/e/home_backup && sha256sum -c SHA256SUMS              # 8+ min per 5 GB of drvfs reads

Single file: `tar -xf <archive> -C $HOME .cptr/app.db` (archive paths are `./`-relative to `$HOME`).

Done end to end on 2026-09-24: `sha256sum -c` → **OK** for the published archive; extracting
`.bashrc`, `AIjly/.env.prod`, `.backupignore`, `.backup-info/MANIFEST.txt`,
`greyhound-odds/data/atr.sqlite` and `.cptr/app.db` took 3:50 (5 GB read + 6.9 GB written).

- `.bashrc` and `AIjly/.env.prod` came back **byte-identical** to live — secrets round-trip.
- `.backupignore` differed, exactly as it should: the archive was built at 07:47 and the file
  was edited at 08:25, so the restore showed the pre-edit copy. Good proof the snapshot is
  point-in-time rather than "whatever is on disk now".
- Both SQLite snapshots pass `PRAGMA quick_check` after extraction: `.cptr/app.db` (0.13 GB,
  15 tables) and the 6.61 GB `greyhound-odds/data/atr.sqlite` (8 tables, `journal_mode=delete`)
  — i.e. the 10.9 GB live WAL database becomes a **standalone, consistent 6.61 GB database**,
  not a torn file copy.
- Extraction is not a substitute for a restore: nothing was written back over `$HOME`, and the
  container data (`dbdumps/`, volume tars) was not replayed. See `RESTORE.md` in the dest dir.

## Knobs

`HOME_BACKUP_DEST`, `HOME_BACKUP_KEEP`, `HOME_BACKUP_COMPRESS` (`auto|xz|xz1|gzip|pigz|none`),
`HOME_BACKUP_WORK`, `HOME_BACKUP_IGNORE`, `HOME_BACKUP_DB_BUDGET`, `HOME_BACKUP_DOCKER`,
`HOME_BACKUP_VOLUMES{,_SKIP,_EXCLUDE}`, `HOME_BACKUP_PYTHON`.

Modes: `--dry-run` (scan + report only, writes nothing outside the work dir), `--why <path>`
(which rule decides a path), `--resume` (re-compress the stage an earlier run left behind — the
fix for a run killed before compressing, saves re-staging 15 GB), `--init-ignore` (write a
starter `~/.backupignore`).

## Killing a run: the flock is inherited, so the wrapper is not the run (2026-09-25)

Observed while relaunching a run that had been started by a plain `nohup … &` from a cptr
tool call: `pkill -f 'bash /home/brendan/home-backup.sh'` matched **only the parent bash**. The
scan `python3` child and its `tee -a backup.log` survived, were reparented to the cptr backend,
and — because they had inherited the open `run.lock` fd — **kept holding the flock**. The
script's `EXIT` trap had already fired in the dead parent (`run ended: exit=0`, i.e. the trap
reports 0 for an externally SIGTERMed script), so nothing was left to build the stage, yet every
relaunch printed only:

    another home-backup run holds the lock — exiting (its own log is current.log)
    run ended: exit=0

So a "held lock" line with no live `bash home-backup.sh` process and no stage progress means an
**orphaned scan child**, not a real overlapping run. Find it with

    for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q run.lock && \
      echo "$p: $(tr '\0' ' ' < $p/cmdline)"; done

and kill those PIDs, not the wrapper. The scan lists (`include.list`, `materialise.list`,
`report.txt`) are regenerated at the start of every run, so killing an orphaned scanner loses
nothing — but a run killed *after* staging starts does need `--resume` (the stage is only in
`.cache/home-backup/stage`, which is `.cache/`-excluded and deleted after a successful publish,
so the scan never sees it).

Launch recipe unchanged, and this is the one that works — the session leader must be the script
itself (`ps -eo pid,sid,cmd` shows `SID == PID`, `Ss`):

    setsid nohup /home/brendan/home-backup.sh </dev/null >/dev/null 2>&1 &

Also worth knowing when reading a live run: `log()` writes `current.log`, but the scan and
materialise passes are piped to `tee -a backup.log`, so the per-database `OK snapshot …` lines,
`stage check: …` and `sqlite snapshots: … ok, … degraded` appear **only in `backup.log`**.
`current.log` stays at ~11 lines until compression starts; grep `backup.log` for the summaries.
