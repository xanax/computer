#!/usr/bin/env bash
#
# home-backup.sh — nightly tar of $HOME, minus anything you can re-download.
# Source of truth: ~/computer/scripts/home-backup.sh    (install: cp to ~/home-backup.sh)
#
# What it does
#   1. walks $HOME applying ignore rules in order: built-in defaults, then ~/.backupignore
#      (gitignore-style — # comment, ! re-include, trailing / = dir only, * ? [..] ** globs,
#       last matching rule wins; no slash = matches at any depth, with a slash = relative to
#       $HOME, leading / also anchors to $HOME)
#   2. copies exactly the surviving entries into a staging tree (rsync --files-from, NUL list,
#      so nothing a rule excluded can sneak back in)
#   3. materialises live SQLite databases as VACUUM INTO / backup-API snapshots instead of
#      copying hot .db/-wal/-shm files
#   4. records every git repo it skipped (.git) with HEAD/branch/stash/unpushed counts, the
#      toolchain versions it excluded, and the ignore rules in force
#   5. tars + compresses the stage, verifies it against the stage, publishes to the dest dir
#      and prunes old archives
#
# Deliberately NOT excluded: .env and other secrets, dotfiles, data dirs, downloads, ~/.ssh,
# source trees, SQLite databases.
# Deliberately excluded by default: .git, node_modules, .venv/venv, __pycache__, pip/npm/yarn
# caches, ~/.cache, VS Code server, platformio packages, OS droppings. Every default is listed
# and commented in ~/.backupignore, and re-includable with a `!` line.
#
# Output: /mnt/e/home_backup/home-backup-YYYYMMDD-HHMM.$EXT (+ SHA256SUMS, backup.log, RESTORE.md)
#
# Env knobs
#   HOME_BACKUP_DEST      default /mnt/e/home_backup
#   HOME_BACKUP_KEEP      nightly archives to retain (default 7, 0 = keep everything)
#   HOME_BACKUP_IGNORE    default $HOME/.backupignore
#   HOME_BACKUP_COMPRESS  auto|xz|xz1|gzip|pigz|none  (auto = xz -T0 -6 when xz and >=4
#                         cores are available — same wall clock as gzip, ~40% smaller;
#                         xz1 = much faster, 27% smaller than gzip; else pigz, else gzip)
#   HOME_BACKUP_WORK      scratch/staging dir (default ~/.cache/home-backup, always excluded)
#                         $WORK/current.log  = this run's log, truncated at start → poll this
#                         $WORK/backup.log   = history across runs (rotated at 5 MB)
#   HOME_BACKUP_SOURCE    tree to back up (default $HOME)
#   HOME_BACKUP_PYTHON    default /usr/bin/python3 (never the workspace venv — it is excluded)
#   HOME_BACKUP_DB_BUDGET seconds one live database may take before it is copied raw
#                         instead (default 1800; a 7GB database being written to
#                         continuously needs a consistent single read pass to finish)
#
# Container knobs (data under /var/lib/docker is root-only and NOT under $HOME, so a
# $HOME tar would miss it — see .backup-info/DOCKER.txt inside each archive)
#   HOME_BACKUP_DOCKER    1|0    capture container volumes + db dumps (default 1)
#   HOME_BACKUP_VOLUMES   all|running|none   named volumes to tar (default all)
#   HOME_BACKUP_VOLUMES_SKIP      extra volume-name globs to skip (space separated)
#   HOME_BACKUP_VOLUMES_EXCLUDE   paths inside a volume to leave out
#                                 (default ./cache ./caches ./.cache ./tmp ./.tmp)
#   HOME_BACKUP_VOLUME_HELPER     image used to tar a volume (default alpine:latest)
#
# Usage
#   home-backup.sh                 run a backup
#   home-backup.sh --dry-run       scan + report only; writes nothing outside the work dir
#   home-backup.sh --why PATH      explain which rule decides PATH (and its ancestors)
#   home-backup.sh --init-ignore   write a starter ~/.backupignore (refuses to overwrite)
#   home-backup.sh --help
#
# Scheduled: cptr automation "Nightly home backup" (FREQ=DAILY;BYHOUR=4;BYMINUTE=15) runs this
# and verifies the archive. See ~/computer/notes/NOTES-home-backup-nightly.md
#
set -euo pipefail

SOURCE="${HOME_BACKUP_SOURCE:-$HOME}"
DEST="${HOME_BACKUP_DEST:-/mnt/e/home_backup}"
KEEP="${HOME_BACKUP_KEEP:-7}"
IGNORE_FILE="${HOME_BACKUP_IGNORE:-$HOME/.backupignore}"
COMPRESS="${HOME_BACKUP_COMPRESS:-auto}"
WORK="${HOME_BACKUP_WORK:-$HOME/.cache/home-backup}"
PYTHON="${HOME_BACKUP_PYTHON:-/usr/bin/python3}"

# container capture (docker volumes + logical dumps of running database containers)
DOCKER_MODE="${HOME_BACKUP_DOCKER:-1}"            # 0 = do not look at containers at all
VOL_MODE="${HOME_BACKUP_VOLUMES:-all}"            # all | running | none
VOL_SKIP_EXTRA="${HOME_BACKUP_VOLUMES_SKIP:-}"    # extra volume-name globs to skip
VOL_EXCL="${HOME_BACKUP_VOLUMES_EXCLUDE:-./cache ./caches ./.cache ./tmp ./.tmp}"
VOL_HELPER="${HOME_BACKUP_VOLUME_HELPER:-alpine:latest}"
VOL_SKIP_BUILTIN='*cache* *-m2 *maven* *trivy* *tmp*'
CTR_VOLS=0; CTR_VOL_BYTES=0; CTR_DUMPS=0; CTR_DUMP_BYTES=0; CTR_SKIPS=0; CTR_FAILS=0
CTR_SUMMARY="not captured"

# rel path of WORK / DEST inside SOURCE — hard-excluded, a rule can never re-include them
if [ "${WORK#"$SOURCE"/}" != "$WORK" ]; then WORK_REL="${WORK#"$SOURCE"/}"; else WORK_REL=""; fi
if [ "${DEST#"$SOURCE"/}" != "$DEST" ]; then DEST_REL="${DEST#"$SOURCE"/}"; else DEST_REL=""; fi

STAMP="$(date +%Y%m%d-%H%M)"
STAGE="$WORK/stage"
OUT="$WORK/out"
LOCKFILE="$WORK/run.lock"
LOG="$WORK/backup.log"        # appended to, across runs (rotated at 5 MB)
RUNLOG="$WORK/current.log"    # this run only, truncated at start: the file to poll
MODE=run
WHY_PATH=""

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run)  MODE=dry ;;
    --resume)      MODE=resume ;;
    --why)         MODE=why; WHY_PATH="${2:-}"
                   [ -n "$WHY_PATH" ] || { echo "--why needs a path" >&2; exit 2; }; shift ;;
    --init-ignore) MODE=init ;;
    -h|--help)     sed -n '3,42p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)             echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

EXT=tar.gz
case "$COMPRESS" in
  # Measured on this box (12 cores, nice -19) on a 687 MB slice of the real stage — 460 MB of
  # the live atr.sqlite plus staged source trees:
  #   tar -b20   | gzip -6      36.6s   150.7 MB      ratio 0.219
  #   tar -b1024 | gzip -6      36.3s   150.7 MB      (tar's -b is noise: 1%)
  #   tar -b1024 | xz -T0 -1    10.6s   109.7 MB      3.5x faster than gzip, 27% smaller
  #   tar -b1024 | xz -T0 -6    44.3s    91.5 MB      39% smaller than gzip, same speed as it
  # So `xz -T0 -6` costs about what gzip costs and writes ~40% less, i.e. ~2 GB less per night
  # and ~14 GB less across the 7 kept archives. Decompression is not a worry: LZMA2 stores
  # incompressible blocks raw, and this stage is >40% incompressible SQLite snapshot, so a
  # full `tar -tf` verify of an xz archive is as quick as a gzip one (measured 687 MB in 0.8 s).
  # The gotcha this replaces: `pigz` is not installed here, so auto was single-threaded `gzip -6`
  # and left 11 of 12 cores idle for ~15 minutes every night.
  auto) if command -v xz >/dev/null 2>&1 && [ "$(nproc)" -ge 4 ]; then
          TARCOMP="xz -T0 -6"; EXT=tar.xz
        elif command -v pigz >/dev/null 2>&1; then TARCOMP="pigz -6 -p$(nproc)"
        else TARCOMP="gzip -6"; fi ;;
  gzip) TARCOMP="gzip -6" ;;
  pigz) TARCOMP="pigz -6 -p$(nproc)" ;;
  xz)   TARCOMP="xz -T0 -6"; EXT=tar.xz ;;
  xz1)  TARCOMP="xz -T0 -1"; EXT=tar.xz ;;
  none) TARCOMP="" ;;
  *)    echo "bad HOME_BACKUP_COMPRESS=$COMPRESS" >&2; exit 2 ;;
esac

[ -x "$PYTHON" ] || { echo "no python3 at $PYTHON" >&2; exit 1; }
mkdir -p "$WORK" "$OUT"

rotate_log() {
  if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 5242880 ]; then mv -f "$LOG" "${LOG}.1"; fi
}
# Every line goes three ways: stdout (so a detached run can be redirected anywhere),
# $LOG (history) and $RUNLOG (this run only, so `tail current.log` is just the live run —
# the script keeps it fresh itself rather than relying on how it was launched).
log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG" | tee -a "$RUNLOG"
}
die() { log "FATAL: $*"; exit 1; }

# A run that is SIGKILLed leaves no trace in the log at all — which is exactly how a
# silently reaped backup looks like a backup that "just stopped". Announce the end from
# the shell itself, so a log whose last line is not `run ended` means the process died.
trap 'rc=$?; log "run ended: exit=$rc"' EXIT
hsz() { numfmt --to=iec --format='%.1f' "${1:-0}" 2>/dev/null || printf '%sB' "${1:-0}"; }

# ── container data: docker volumes + live database dumps ─────────────────────
#
# /var/lib/docker is root-only and outside $HOME: a home tar cannot see Postgres
# clusters, Neo4j stores or app volumes, and they are not re-downloadable either.
# This phase puts them INSIDE the archive, under .backup-info/:
#   dbdumps/   logical dump of each running database container (pg_dumpall / BGSAVE)
#   docker/    tar.gz of every named volume, minus cache dirs inside it
#   DOCKER.txt what was captured, what was skipped and why, and how to restore
# Nothing here can abort a backup: every failure is a WARN, recorded in DOCKER.txt.
docker_available() {
  command -v docker >/dev/null 2>&1 && timeout 60 docker info >/dev/null 2>&1
}

docker_phase() {
  local plan="${1:-capture}"
  local n c state image pat reason helper="$VOL_HELPER" out err sz user rdir i e dumped_here
  local -a vols=() allc=() running=() lines=() dumps=() ex=()
  local -A owner=() state_of=() dumped=() vsize=()

  if [ "$DOCKER_MODE" = 0 ]; then
    CTR_SUMMARY="disabled (HOME_BACKUP_DOCKER=0)"
    [ "$plan" = capture ] && printf 'container capture disabled with HOME_BACKUP_DOCKER=0\n' > "$INFO/DOCKER.txt"
    return 0
  fi
  if ! docker_available; then
    CTR_SUMMARY="not captured (docker unreachable)"
    log "docker: not reachable — container volumes/databases are NOT in this archive"
    [ "$plan" = capture ] && {
      echo "docker was not reachable during this run, so container data is NOT in this"
      echo "archive. /var/lib/docker is root-only: only a running daemon can read it."
      echo "generated: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    } > "$INFO/DOCKER.txt"
    return 0
  fi

  mapfile -t allc < <(docker ps -a --format '{{.Names}}' 2>/dev/null | LC_ALL=C sort)
  mapfile -t running < <(docker ps --format '{{.Names}}' 2>/dev/null | LC_ALL=C sort)
  mapfile -t vols < <(docker volume ls -q 2>/dev/null | LC_ALL=C sort)

  # which container mounts which volume (and is that container up?)
  for c in "${allc[@]:-}"; do
    [ -n "$c" ] || continue
    state_of["$c"]="$(docker inspect --format '{{.State.Status}}' "$c" 2>/dev/null)"
    while IFS= read -r src; do
      [ -n "$src" ] || continue
      n="${src##*/volumes/}"; n="${n%%/_data}"
      owner["$n"]="$c"
    done < <(docker inspect --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Source}}{{"\n"}}{{end}}{{end}}' "$c" 2>/dev/null)
  done

  # docker's own size table (one call, no container starts) for the report
  while read -r n sz; do vsize["$n"]="$sz"; done < <(
    timeout 120 docker system df -v 2>/dev/null | awk '
      /^Local Volumes space usage:/ { f = 1; next }
      /space usage:/                { f = 0 }
      f && NF >= 3                  { print $1, $NF }')

  # ── logical dumps of running database containers ────────────────────────────
  [ "$plan" = capture ] && mkdir -p "$INFO/dbdumps"
  for c in "${running[@]:-}"; do
    [ -n "$c" ] || continue
    image="$(docker inspect --format '{{.Config.Image}}' "$c" 2>/dev/null)"
    case "$image" in
      *postgres*|*postgis*)
        user="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$c" 2>/dev/null \
                | sed -n 's/^POSTGRES_USER=//p' | head -1)"
        [ -n "$user" ] || user=postgres
        if [ "$plan" = plan ]; then
          dumps+=("$(printf '%-26s would pg_dumpall -U %s  (%s)' "$c" "$user" "$image")")
          dumped["$c"]="dbdumps/$c.sql.gz"        # predicted, so the volume is not double-counted
          continue
        fi
        out="$INFO/dbdumps/$c.sql.gz"
        # -u postgres is the OS user inside the image, where socket auth is trust
        if timeout 1800 docker exec -u postgres "$c" pg_dumpall -U "$user" 2>"$WORK/dump-$c.err" \
             | gzip -6 > "$out" && [ -s "$out" ] && gzip -t "$out" 2>>"$WORK/dump-$c.err"; then
          sz="$(stat -c%s "$out")"
          CTR_DUMPS=$((CTR_DUMPS + 1)); CTR_DUMP_BYTES=$((CTR_DUMP_BYTES + sz))
          dumped["$c"]="dbdumps/$c.sql.gz"
          dumps+=("$(printf '%-26s pg_dumpall -U %-10s %-28s %s' "$c" "$user" "dbdumps/$c.sql.gz" "$(hsz "$sz")")")
          log "docker: dumped database cluster $c -> $c.sql.gz ($(hsz "$sz"))"
        else
          rm -f "$out"; CTR_FAILS=$((CTR_FAILS + 1))
          dumps+=("$(printf '%-26s FAILED pg_dumpall -U %s: %s' "$c" "$user" \
                    "$(head -c 300 "$WORK/dump-$c.err" 2>/dev/null | tr '\n' ' ')")")
          log "WARN docker: pg_dumpall failed for $c — see DOCKER.txt"
        fi
      ;;
      *redis*|*falkor*)
        rdir="$(docker exec "$c" redis-cli CONFIG GET dir 2>/dev/null | tail -1)"
        [ -n "$rdir" ] || rdir=/data
        if [ "$plan" = plan ]; then
          dumps+=("$(printf '%-26s would snapshot %s into %s/dump.rdb' "$c" "$image" "$rdir")")
          dumped["$c"]="dbdumps/$c-dump.rdb"      # predicted, so the volume is not double-counted
          continue
        fi
        out="$INFO/dbdumps/$c-dump.rdb"
        if timeout 300 docker exec "$c" redis-cli BGSAVE >/dev/null 2>&1; then
          for i in $(seq 1 60); do
            docker exec "$c" redis-cli INFO persistence 2>/dev/null \
              | grep -q 'rdb_bgsave_in_progress:0' && break
            sleep 1
          done
          if timeout 300 docker cp "$c:$rdir/dump.rdb" "$out" 2>"$WORK/dump-$c.err" && [ -s "$out" ]; then
            sz="$(stat -c%s "$out")"
            CTR_DUMPS=$((CTR_DUMPS + 1)); CTR_DUMP_BYTES=$((CTR_DUMP_BYTES + sz))
            dumped["$c"]="dbdumps/$c-dump.rdb"
            dumps+=("$(printf '%-26s BGSAVE %-24s %-28s %s' "$c" "$image" "dbdumps/$c-dump.rdb" "$(hsz "$sz")")")
            log "docker: snapshotted key-value store $c -> $c-dump.rdb ($(hsz "$sz"))"
          else
            rm -f "$out"; CTR_FAILS=$((CTR_FAILS + 1))
            dumps+=("$(printf '%-26s FAILED dump.rdb copy: %s' "$c" \
                      "$(head -c 200 "$WORK/dump-$c.err" 2>/dev/null | tr '\n' ' ')")")
            log "WARN docker: could not snapshot $c — see DOCKER.txt"
          fi
        else
          CTR_FAILS=$((CTR_FAILS + 1))
          dumps+=("$(printf '%-26s FAILED redis BGSAVE' "$c")")
          log "WARN docker: BGSAVE refused by $c"
        fi
      ;;
    esac
  done

  # ── volumes ─────────────────────────────────────────────────────────────────
  [ "$plan" = capture ] && mkdir -p "$INFO/docker"
  for n in "${vols[@]:-}"; do
    [ -n "$n" ] || continue
    c="${owner[$n]:-}"; state=""; dumped_here=""
    if [ -n "$c" ]; then
      state="${state_of[$c]:-}"
      dumped_here="${dumped[$c]:-}"
    fi
    reason=""
    for pat in $VOL_SKIP_BUILTIN $VOL_SKIP_EXTRA; do
      [ -n "$pat" ] || continue
      case "$n" in $pat) reason="re-downloadable (pattern '$pat')" ;; esac
    done
    if [ -z "$reason" ] && [ "$VOL_MODE" = none ]; then
      reason="disabled (HOME_BACKUP_VOLUMES=none)"
    fi
    if [ -z "$reason" ] && [ "$VOL_MODE" = running ] && [ -n "$state" ] && [ "$state" != running ]; then
      reason="container $c not running (HOME_BACKUP_VOLUMES=running)"
    fi
    if [ -z "$reason" ] && [ "$state" = running ] && [ -n "$dumped_here" ]; then
      reason="inside ${dumped_here} (logical dump is smaller)"
    fi
    if [ -n "$reason" ]; then
      CTR_SKIPS=$((CTR_SKIPS + 1))
      lines+=("$(printf '%-34s %-9s SKIP    %s' "$n" "${vsize[$n]:-?}" "$reason")")
      continue
    fi
    if [ "$plan" = plan ]; then
      lines+=("$(printf '%-34s %-9s WOULD CAPTURE%s' "$n" "${vsize[$n]:-?}" \
                "${c:+  (mounted by $c, $state)}")")
      continue
    fi
    out="$INFO/docker/$n.tar.gz"; err="$WORK/volume-$n.err"
    ex=()
    for e in $VOL_EXCL; do [ -n "$e" ] && ex+=(--exclude="$e"); done
    if timeout 3600 docker run --rm --entrypoint tar -v "$n:/v:ro" "$helper" "${ex[@]}" \
         -czf - -C /v . >"$out" 2>"$err" && gzip -t "$out" 2>>"$err"; then
      sz="$(stat -c%s "$out")"
      if [ "$sz" -lt 1024 ]; then
        rm -f "$out"; CTR_SKIPS=$((CTR_SKIPS + 1))
        lines+=("$(printf '%-34s %-9s SKIP    empty' "$n" "$(hsz "$sz")")")
      else
        CTR_VOLS=$((CTR_VOLS + 1)); CTR_VOL_BYTES=$((CTR_VOL_BYTES + sz))
        lines+=("$(printf '%-34s %-9s captured%s' "$n" "$(hsz "$sz")" \
                  "${c:+  (mounted by $c, $state)}")")
      fi
    else
      rm -f "$out"; CTR_FAILS=$((CTR_FAILS + 1))
      lines+=("$(printf '%-34s %-9s FAILED  %s' "$n" "?" \
                "$(head -c 200 "$err" 2>/dev/null | tr '\n' ' ')")")
      log "WARN docker: volume $n not captured — see DOCKER.txt"
    fi
  done

  if [ "$plan" = plan ]; then
    log "container data (dry run — nothing captured, nothing written):"
    for i in "${dumps[@]:-}"; do [ -n "$i" ] && log "  $i"; done
    for i in "${lines[@]:-}"; do [ -n "$i" ] && log "  $i"; done
    return 0
  fi

  CTR_SUMMARY="$CTR_VOLS volume(s) $(hsz "$CTR_VOL_BYTES") + $CTR_DUMPS db dump(s) $(hsz "$CTR_DUMP_BYTES")"
  [ "$CTR_FAILS" -eq 0 ] || CTR_SUMMARY="$CTR_SUMMARY, $CTR_FAILS FAILED"
  log "docker: $CTR_SUMMARY, $CTR_SKIPS skipped"

  {
    echo "container data that lives outside \$HOME"
    echo "======================================="
    echo "generated   : $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "docker      : $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
    echo "policy      : HOME_BACKUP_DOCKER=$DOCKER_MODE  HOME_BACKUP_VOLUMES=$VOL_MODE"
    echo "volume excl : $VOL_EXCL"
    echo "name skips  : $VOL_SKIP_BUILTIN $VOL_SKIP_EXTRA"
    echo "captured    : $CTR_SUMMARY"
    echo "skipped     : $CTR_SKIPS"
    echo
    echo "/var/lib/docker is root-only, so a \$HOME tar cannot see any of this: every named"
    echo "volume is tarred (minus the cache dirs listed above) and every running database"
    echo "container is dumped logically. Images are NOT here — rebuild them from the"
    echo "Dockerfiles/compose files, which are in this archive under \$HOME."
    echo
    if [ "$CTR_FAILS" -gt 0 ]; then
      echo "NOTE: $CTR_FAILS item(s) FAILED above and fell back to a raw volume tar. If the"
      echo "      container was still running, that tar may be inconsistent — stop it and"
      echo "      re-capture before trusting the data."
      echo
    fi
    echo "--- database dumps (taken while the container was running) ---"
    if [ "${#dumps[@]}" -gt 0 ]; then printf '%s\n' "${dumps[@]}"; else echo "(none)"; fi
    echo
    echo "--- volumes ---"
    if [ "${#lines[@]}" -gt 0 ]; then printf '%s\n' "${lines[@]}"; else echo "(none)"; fi
    echo
    echo "--- containers on this host ---"
    docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null \
      | while IFS='|' read -r c image state; do
          printf '%-26s %-42s %s\n' "$c" "$image" "$state"
          while IFS= read -r e; do
            [ -z "$e" ] || printf '    mount  %s\n' "$e"
          done < <(docker inspect --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' "$c" 2>/dev/null)
        done
    echo
    echo "--- not covered by this archive ---"
    echo "* data behind Windows-side bind mounts (see the mount list above)"
    echo "* image layers (rebuild with docker compose build / docker build)"
    echo "* volumes skipped by name pattern. Re-include everything with:"
    echo "    HOME_BACKUP_VOLUMES=all HOME_BACKUP_VOLUMES_SKIP='' ~/home-backup.sh"
    echo
    echo "--- restore ---"
    echo "# one volume, from the root of an extracted archive"
    echo "docker run --rm -i --entrypoint tar -v <volume>:/v alpine -xzf - -C /v \\"
    echo "    < .backup-info/docker/<volume>.tar.gz"
    echo "# one database cluster into a running postgres container"
    echo "gunzip -c .backup-info/dbdumps/<container>.sql.gz \\"
    echo "    | docker exec -i -u postgres <container> psql -U <POSTGRES_USER> -d postgres"
    echo "# a redis/falkordb snapshot"
    echo "docker cp .backup-info/dbdumps/<container>-dump.rdb <container>:/data/dump.rdb  # then restart it"
  } > "$INFO/DOCKER.txt"

  # This phase promises that nothing in it can abort a backup, and the script runs under
  # `set -e`. So never let the caller see a non-zero status — a failing probe inside the
  # report block (an empty last line from `docker ps`, say) used to end the whole run.
  return 0
}

# ── ignore engine + scanner (one embedded program, three modes) ───────────────
scanner() {
  "$PYTHON" - "$SOURCE" "$IGNORE_FILE" "$WORK" "$MODE" "$WHY_PATH" \
               "$WORK_REL:$DEST_REL" "$IGNORE_FILE" <<'PY'
#!/usr/bin/env python3
"""Ignore engine (gitignore semantics) and $HOME scanner for home-backup.sh."""
import json, os, re, stat, sys

args = sys.argv[1:]
HOME, IGNORE_FILE, WORK, MODE, WHY = (args + [''] * 8)[:5]
ALWAYS = args[5] if len(args) > 5 else ''
INIT_TARGET = args[6] if len(args) > 6 else ''

# ── built-in defaults (documented in the starter ~/.backupignore) ─────────────
DEFAULTS = r"""
# ── version-control metadata (re-clone; .backup-info/REPOS.txt records every repo + HEAD) ──
.git/
.git-*
.svn/
.hg/
# ── language / build vendor trees (reinstallable) ──
node_modules/
.venv/
venv/
.virtualenv/
__pycache__/
*.pyc
*.pyo
*.egg-info/
.eggs/
.tox/
.nox/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.pylint.d/
.ipynb_checkpoints/
.npm/
.yarn/
.pnpm-store/
.cargo/registry/
.gradle/
.m2/repository/
.nuget/
.vscode-server/
.vscode-remote-containers/
.platformio/packages/
.platformio/.cache/
# ── caches (regenerable) ──
.cache/
.local/share/Trash/
.Trash/
# ── OS / editor droppings ──
.DS_Store
Thumbs.db
desktop.ini
$RECYCLE.BIN/
System Volume Information/
*.swp
*.swo
*~
# ── installed-from-a-registry toolchains (re-listed in .backup-info/TOOLS.txt) ──
.npm-global/
# ── never back up the backup's own bookkeeping ──
.backup-info/
""".strip('\n').splitlines()

EXT_DB = {'.db', '.db3', '.sqlite', '.sqlite2', '.sqlite3'}
SIDECARS = ('-wal', '-shm', '-journal')
SQLITE_MAGIC = b'SQLite format 3\x00'
UNITS = ((1 << 40, 'T'), (1 << 30, 'G'), (1 << 20, 'M'), (1 << 10, 'K'))


def human(n):
    for unit, letter in UNITS:
        if n >= unit:
            return f"{n / unit:.1f}{letter}"
    return f"{n}B"


def glob_to_regex(pat):
    out, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        if c == '\\' and i + 1 < n:                       # escapes: \# \! \* \space
            out.append(re.escape(pat[i + 1])); i += 2; continue
        if c == '*':
            if pat[i:i + 2] == '**':
                if i + 2 < n and pat[i + 2] == '/':
                    out.append('(?:[^/]+/)*'); i += 3; continue    # **/ = any depth (incl. none)
                out.append('.*'); i += 2; continue
            out.append('[^/]*'); i += 1; continue
        if c == '?':
            out.append('[^/]'); i += 1; continue
        if c == '[':
            j = i + 1
            if j < n and pat[j] in '!^':
                j += 1
            if j < n and pat[j] == ']':
                j += 1
            while j < n and pat[j] != ']':
                j += 1
            if j >= n:                                     # unbalanced '[' is a literal
                out.append(re.escape(c)); i += 1; continue
            body = pat[i + 1:j]
            if body.startswith('!'):
                body = '^' + body[1:]
            out.append('[' + body + ']'); i = j + 1; continue
        out.append(re.escape(c)); i += 1
    return ''.join(out)


class Rule:
    __slots__ = ('text', 'origin', 'negate', 'dir_only', 'regex')

    def __init__(self, text, origin):
        self.text, self.origin = text, origin
        raw = text
        self.negate = raw.startswith('!')
        if self.negate:
            raw = raw[1:]
        self.dir_only = raw.endswith('/')
        if self.dir_only:
            raw = raw[:-1]
        # a trailing slash is not an anchor: 'node_modules/' matches at any depth, '/x' does not
        anchored = raw.startswith('/') or '/' in raw
        raw = raw.strip('/')
        body = glob_to_regex(raw)
        self.regex = re.compile(('^' + body + '$') if anchored else ('(?:^|/)' + body + '$'))

    def matches(self, rel, is_dir):
        if self.dir_only and not is_dir:
            return False
        return self.regex.search(rel) is not None

    def describe(self):
        return f"{self.text}   [{self.origin}]"


def parse_rules(text, origin):
    rules = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.rstrip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('\\#') or line.startswith('\\!'):
            line = line[1:]
        where = origin if origin == 'built-in' else f"{origin}:{lineno}"
        rules.append(Rule(line, where))
    return rules


def load_rules():
    base = parse_rules('\n'.join(DEFAULTS), 'built-in')
    extra = []
    if os.path.isfile(IGNORE_FILE):
        try:
            with open(IGNORE_FILE, 'r', errors='replace') as fh:
                extra = parse_rules(fh.read(), IGNORE_FILE)
        except OSError:
            extra = []
    return base + extra, len(base), len(extra)


def decide(rules, rel, is_dir):
    """Last matching rule wins (gitignore). Returns the winning Rule, or None."""
    winner = None
    for r in rules:
        if r.matches(rel, is_dir):
            winner = r
    return winner


def tree_size(path):
    total = files = 0
    for dirpath, _dirnames, filenames in os.walk(path, onerror=lambda e: None):
        for f in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, f))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size; files += 1
    return total, files


def is_sqlite(path):
    try:
        with open(path, 'rb') as fh:
            return fh.read(16) == SQLITE_MAGIC
    except OSError:
        return False


# ── mode: why ────────────────────────────────────────────────────────────────
def do_why(path):
    rules, n_base, n_extra = load_rules()
    full = os.path.abspath(path)
    home = os.path.abspath(HOME) + '/'
    rel = full[len(home):] if full.startswith(home) else full.lstrip('/')
    rel = rel.rstrip('/')
    exists = os.path.exists(os.path.join(HOME, rel))
    is_dir = os.path.isdir(os.path.join(HOME, rel))
    if not exists:
        is_dir = True                      # match dir-only rules too (that is usually the intent)
    print(f"path      : {rel}{'/' if is_dir else ''}"
          f"{'' if exists else '   (not on disk — matching by name, as a directory)'}")
    print(f"rules     : {n_base} built-in + {n_extra} from {IGNORE_FILE}")
    parts = rel.split('/')
    ancestors = ['/'.join(parts[:i]) for i in range(1, len(parts))]
    blocked = None
    for anc in ancestors:
        r = decide(rules, anc, True)
        if r is not None and not r.negate:
            blocked = (anc, r)
    hits = [r for r in rules if r.matches(rel, is_dir)]
    win = decide(rules, rel, is_dir)
    print("matching rules, in evaluation order (last match wins):")
    if not hits:
        print("  (none)")
    for r in hits:
        print(f"  {'IGNORE ' if not r.negate else 'RE-INCLUDE'}  {r.describe()}"
              f"{'   <- wins' if r is win else ''}")
    if blocked and (win is None or win.negate):
        print(f"verdict   : IGNORED — ancestor '{blocked[0]}/' is excluded by "
              f"'{blocked[1].text}' [{blocked[1].origin}].\n"
              f"            gitignore (and this script) cannot re-include inside an excluded\n"
              f"            directory: re-include '{blocked[0]}/' too, or use a narrower rule.")
    elif win is None:
        print("verdict   : INCLUDED (no rule matches it or any of its ancestors)")
    else:
        print(f"verdict   : {'IGNORED' if not win.negate else 'INCLUDED'} by {win.describe()}")
    return 0


# ── mode: init ───────────────────────────────────────────────────────────────
def do_init(target):
    header = """\
# ~/.backupignore — what the nightly home backup skips.
#
# Read by ~/home-backup.sh (cptr automation "Nightly home backup", 04:15). gitignore-style;
# rules are evaluated in order and the LAST MATCH WINS:
#
#   # comment                  ignored
#   name                       any file or dir called `name`, at any depth
#   dir/                       only directories called `dir`
#   /path/from/home            a slash anywhere anchors the pattern to $HOME
#   *.log   **/tmp   ?x  [ab]  globs; * and ? never cross '/', ** does
#   !something                 re-include (negation) — must come AFTER the rule it undoes
#
# Never excluded no matter what: .env and other secrets, dotfiles, data dirs, SQLite
# databases (snapshotted consistently), downloads, ~/.ssh, ~/.config, source trees.
#
# Two gotchas:
#   * a negation cannot re-include a file inside an excluded directory — re-include the
#     directory as well (`!node_modules/` before `!node_modules/mine/`).
#   * the backup's own work dir (~/.cache/home-backup) and its destination are always skipped.
#
# Put your own exclusions under "your overrides"; they beat the defaults listed at the
# bottom (which the script applies whether or not they are written here).
# Use `~/home-backup.sh --why <path>` to see which rule decides any file.
# ---------------------------------------------------------------------------------------

# ── your overrides ──
# An active rule must start in column 0 and carry NO trailing comment — the whole line is the
# pattern, so explain it on the line above.
#
# Vendor *installations*, not data you authored; each is program-only and re-installable:
#   /opt/calibre            625M  the calibre program dir (config is ~/.config/calibre)
#   /.local/playwright-deps 178M  root tree from `playwright install-deps`; re-run it after a restore
#   .grok/downloads         471M  the grok CLI install cache — but ~/.local/bin/grok is a symlink
#                                 into it, so excluding it leaves `grok` dangling after a restore
#
# cache/                     # any dir literally named `cache` (e.g. computer/.cptr/cache, TTS audio)
# opt/calibre/               # same as the specific rule above, by whole tree
# *.iso
# offline_search/            # 1.5G offline corpus — drop it only if you can rebuild it
# !.npm-global/              # keep globally-installed npm CLIs (re-listed in TOOLS.txt)

# ---------------------------------------------------------------------------------------
# built-in defaults, already applied by the script — uncomment a '!' line to re-include
# ---------------------------------------------------------------------------------------
"""
    body = [line if line.startswith('#') else '# ' + line for line in DEFAULTS]
    body.append('')
    body.append('# !cptr-pre-unify-20260922.db    # 115M one-off pre-migration DB copy')
    with open(target, 'w') as fh:
        fh.write(header + '\n'.join(body) + '\n')
    print(f"wrote {target} ({len(DEFAULTS)} defaults documented, all commented out)")
    return 0


# ── mode: scan ───────────────────────────────────────────────────────────────
def do_scan():
    rules, n_base, n_extra = load_rules()
    always = [p for p in ALWAYS.split(':') if p]
    inc_fh = open(os.path.join(WORK, 'include.list'), 'wb')
    mat_fh = open(os.path.join(WORK, 'materialise.list'), 'wb')
    env_fh = open(os.path.join(WORK, 'envfiles.list'), 'wb')
    excl_fh = open(os.path.join(WORK, 'excluded.list'), 'wb')

    root_dev = os.stat(HOME).st_dev
    counts = dict(files=0, dirs=0, symlinks=0, excluded=0, otherfs=0, special=0,
                  unreadable=0, internal=0, db_skipped=0, db_sidecar=0, newline_paths=0)
    included_bytes = excluded_bytes = 0
    db_list, external_links, newline_paths, warns = [], [], [], []
    sqlite_set, by_rule, examples, top = set(), {}, {}, {}

    def add_stat(fh, rel):
        fh.write(rel.encode() + b'\0')

    stack = ['']
    while stack:
        rel = stack.pop()
        adir = os.path.join(HOME, rel) if rel else HOME
        try:
            with os.scandir(adir) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            counts['unreadable'] += 1
            warns.append(f"unreadable dir: {rel or '.'} ({exc.strerror})")
            continue
        for ent in entries:
            crel = f"{rel}/{ent.name}" if rel else ent.name
            if always and any(crel == a or crel.startswith(a + '/') for a in always):
                counts['internal'] += 1
                continue
            try:
                st = ent.stat(follow_symlinks=False)
            except OSError:
                counts['unreadable'] += 1
                continue
            mode = st.st_mode
            is_link, is_dir = stat.S_ISLNK(mode), stat.S_ISDIR(mode)

            if is_dir and not is_link:
                if st.st_dev != root_dev:
                    counts['otherfs'] += 1
                    continue
                r = decide(rules, crel, True)
                if r is not None and not r.negate:
                    size, nfiles = tree_size(os.path.join(HOME, crel))
                    key = f"{r.text}\t{r.origin}"
                    rec = by_rule.setdefault(key, [0, 0])
                    rec[0] += 1 + nfiles
                    rec[1] += size
                    examples.setdefault(key, []).append(crel + '/')
                    counts['excluded'] += 1
                    excluded_bytes += size
                    excl_fh.write((crel + '/\n').encode())
                    continue
                add_stat(inc_fh, crel)
                counts['dirs'] += 1
                stack.append(crel)
                continue

            if not is_link and not stat.S_ISREG(mode):
                counts['special'] += 1
                continue

            r = decide(rules, crel, False)
            if r is not None and not r.negate:
                key = f"{r.text}\t{r.origin}"
                rec = by_rule.setdefault(key, [0, 0])
                rec[0] += 1
                rec[1] += st.st_size
                examples.setdefault(key, []).append(crel)
                counts['excluded'] += 1
                excluded_bytes += st.st_size
                excl_fh.write((crel + '\n').encode())
                continue

            name, ext = ent.name, os.path.splitext(ent.name)[1].lower()
            if ext in EXT_DB and is_sqlite(os.path.join(HOME, crel)):
                sqlite_set.add(crel)
                db_list.append((crel, st.st_size))
                mat_fh.write(f"sqlite\t{crel}\0".encode())
                counts['db_skipped'] += 1
                included_bytes += st.st_size
                topkey = crel.split('/', 1)[0]
                top[topkey] = top.get(topkey, 0) + st.st_size
                continue
            if name.endswith(SIDECARS) and crel[:crel.rindex('-')] in sqlite_set:
                mat_fh.write(f"raw\t{crel}\0".encode())      # only after its db was snapshotted
                counts['db_sidecar'] += 1
                continue

            if name.startswith('.env'):
                env_fh.write(crel.encode() + b'\0')
            add_stat(inc_fh, crel)
            if is_link:
                counts['symlinks'] += 1
                target = os.readlink(os.path.join(HOME, crel))
                # resolve relative to the LINK's directory, not the cwd
                linkdir = os.path.dirname(os.path.join(HOME, crel))
                resolved = os.path.normpath(os.path.join(linkdir, target))
                if not (resolved == HOME or resolved.startswith(HOME + '/')):
                    external_links.append(f"{crel} -> {target}")
            else:
                counts['files'] += 1
                included_bytes += st.st_size
                topkey = crel.split('/', 1)[0]
                top[topkey] = top.get(topkey, 0) + st.st_size
                if '\n' in crel:
                    counts['newline_paths'] += 1
                    newline_paths.append(crel)

    for fh in (inc_fh, mat_fh, env_fh, excl_fh):
        fh.close()

    L = ["=== home backup scan ==="]
    L.append(f"source            : {HOME}")
    L.append(f"ignore rules      : {n_base} built-in + {n_extra} from {IGNORE_FILE}")
    L.append(f"included          : {counts['files']:,} files + {counts['dirs']:,} dirs + "
             f"{counts['symlinks']:,} symlinks = {human(included_bytes)}")
    L.append(f"excluded          : {counts['excluded']:,} entries = {human(excluded_bytes)}"
             f"  (.git, vendor trees, caches — see below)")
    if counts['db_skipped']:
        L.append(f"sqlite            : {counts['db_skipped']} live database(s) snapshotted via "
                 f"VACUUM INTO / backup API ({counts['db_sidecar']} -wal/-shm folded in, not copied)")
    if counts['otherfs']:
        L.append(f"other filesystems : {counts['otherfs']} mount points skipped (-xdev)")
    if counts['special']:
        L.append(f"skipped specials  : {counts['special']} sockets/fifos/devices")
    if counts['unreadable']:
        L.append(f"unreadable        : {counts['unreadable']} (permission denied)")
    L.append("")
    L.append(f"excluded by rule, biggest first — re-include with a `!` line in {IGNORE_FILE}:")
    ranked = sorted(by_rule.items(), key=lambda kv: -kv[1][1])
    for key, (npaths, size) in ranked[:25]:
        text, origin = key.split('\t')
        where = 'built-in' if origin == 'built-in' else os.path.basename(origin.rsplit(':', 1)[0])
        L.append(f"  {human(size):>7}  {npaths:>8,} entries  {text:<26} [{where}]")
        for ex in examples.get(key, [])[:2]:
            L.append(f"                                e.g. {ex}")
    if len(ranked) > 25:
        L.append(f"  ... and {len(ranked) - 25} more rule(s); full list in .backup-info/EXCLUSIONS.txt")
    L.append("")
    L.append("biggest INCLUDED items — add to ~/.backupignore if you can re-download them:")
    for name, size in sorted(top.items(), key=lambda kv: -kv[1])[:15]:
        L.append(f"  {human(size):>7}  {name}")
    if db_list:
        L.append("")
        L.append("live sqlite databases (snapshotted, not raw-copied):")
        for rel, size in sorted(db_list, key=lambda t: -t[1])[:15]:
            L.append(f"  {human(size):>7}  {rel}")
    if external_links:
        L.append("")
        L.append("symlinks pointing outside $HOME (stored as links; targets NOT backed up):")
        for s in external_links[:10]:
            L.append(f"  {s}")
    if newline_paths:
        L.append("")
        L.append(f"WARNING: {len(newline_paths)} path(s) contain a newline (tar/rsync cope; "
                 f"text tools may not):")
        for p in newline_paths[:5]:
            L.append("  " + p.replace('\n', '\\n'))
    if warns:
        L.append("")
        L.extend(("WARN " + w) for w in warns[:10])
        if len(warns) > 10:
            L.append(f"WARN ... and {len(warns) - 10} more unreadable paths")

    report = '\n'.join(L)
    with open(os.path.join(WORK, 'report.txt'), 'w') as fh:
        fh.write(report + '\n')
    with open(os.path.join(WORK, 'rules.txt'), 'w') as fh:
        fh.write(f"# ignore rules in effect: {n_base} built-in, then {IGNORE_FILE}\n")
        fh.write("# columns: rule <TAB> where it came from\n")
        for r in rules:
            fh.write(f"{'!' if r.negate else ''}{r.text}\t{r.origin}\n")
    with open(os.path.join(WORK, 'scan.env'), 'w') as fh:
        fh.write(f"SCAN_INCLUDED_BYTES={included_bytes}\n")
        fh.write(f"SCAN_EXCLUDED_BYTES={excluded_bytes}\n")
        fh.write(f"SCAN_FILES={counts['files']}\n")
        fh.write(f"SCAN_DIRS={counts['dirs']}\n")
        fh.write(f"SCAN_SYMLINKS={counts['symlinks']}\n")
        fh.write(f"SCAN_ENTRIES={counts['files'] + counts['dirs'] + counts['symlinks']}\n")
        fh.write(f"SCAN_DBS={counts['db_skipped']}\n")
        fh.write(f"SCAN_EXCLUDED={counts['excluded']}\n")
    with open(os.path.join(WORK, 'scan.json'), 'w') as fh:
        json.dump(dict(counts=counts, included_bytes=included_bytes,
                       excluded_bytes=excluded_bytes, by_rule=by_rule, top=top,
                       dbs=[{'path': p, 'bytes': s} for p, s in db_list],
                       external_links=external_links, newline_paths=newline_paths,
                       rules=n_base + n_extra), fh, indent=1)
    print(report)
    return 0


if MODE == 'why':
    sys.exit(do_why(WHY))
if MODE == 'init':
    sys.exit(do_init(INIT_TARGET))
sys.exit(do_scan())
PY
}

# ── modes that do not need the lock or the destination ────────────────────────
if [ "$MODE" = why ]; then scanner; exit $?; fi
if [ "$MODE" = init ]; then
  if [ -e "$IGNORE_FILE" ]; then echo "$IGNORE_FILE already exists — leaving it alone" >&2; exit 1; fi
  scanner; exit $?
fi

# ── single instance ──────────────────────────────────────────────────────────
exec 9>"$LOCKFILE"
if ! flock -n 9; then log "another home-backup run holds the lock — exiting (its own log is current.log)"; exit 0; fi
rotate_log
: > "$RUNLOG"   # only after the lock: a second run must never wipe the live run's log
log "=== home backup start (pid $$, mode $MODE, compress $COMPRESS) ==="

# ── --resume: reuse the stage an earlier run already built ────────────────────
# The expensive part of a run is the walk, the 15 GB rsync and a consistent snapshot of
# every live database. If a run dies after that (a destination that is offline, a bad tar),
# `--resume` skips straight to compressing the stage that is already there instead of
# paying for all of it again. It never re-scans, so the counts in the manifest come from
# the staged tree itself; the archive is still verified against that tree before publishing.
RESUMED=0
if [ "$MODE" = resume ]; then
  [ -d "$STAGE" ] || die "--resume: there is no stage at $STAGE to resume from"
  [ -f "$WORK/report.txt" ] || die "--resume: $WORK/report.txt is missing — run a full backup first"
  RESUMED=1
fi

if [ "$RESUMED" = 0 ]; then
# ── scan ─────────────────────────────────────────────────────────────────────
log "scanning $SOURCE (rules: built-in + $IGNORE_FILE)"
scanner 2>&1 | tee -a "$LOG"
# shellcheck disable=SC1091
source "$WORK/scan.env"
ENTRIES_EQ="${SCAN_ENTRIES:-0}"
[ "$ENTRIES_EQ" -gt 100 ] || die "scan found almost nothing ($ENTRIES_EQ entries) — refusing to build an empty backup"
log "scan: $(hsz "$SCAN_INCLUDED_BYTES") to archive across $SCAN_ENTRIES entries; " \
    "$(hsz "$SCAN_EXCLUDED_BYTES") excluded ($SCAN_EXCLUDED entries)"
fi

if [ "$MODE" = dry ]; then
  docker_phase plan
  log "DRY RUN — nothing written outside $WORK; report: $WORK/report.txt"
  log "=== dry run done ==="
  exit 0
fi

# ── destination checks (E: can be missing in WSL) ────────────────────────────
if [ ! -d /mnt/e ] && [ "${DEST#/mnt/e}" != "$DEST" ]; then
  die "/mnt/e is not mounted — cannot back up (start the E: drive / restart WSL)"
fi
mkdir -p "$DEST" || die "cannot create $DEST"
[ -w "$DEST" ] || die "$DEST is not writable"

# ── stage exactly the selected entries ───────────────────────────────────────
if [ "$RESUMED" = 0 ]; then
rm -rf "$STAGE"
mkdir -p "$STAGE"
log "staging selected entries (rsync --files-from)..."
RSYNC_RC=0
rsync -lptgo --from0 --files-from="$WORK/include.list" "$SOURCE/" "$STAGE/" || RSYNC_RC=$?
case "$RSYNC_RC" in
  0)      : ;;
  23|24)  log "WARN rsync reported vanished/unreadable files (rc=$RSYNC_RC) — continuing" ;;
  *)      die "rsync failed (rc=$RSYNC_RC)" ;;
esac
log "staged: $(du -sh "$STAGE" | cut -f1)"

# ── materialise what rsync was told to skip (live sqlite dbs + their sidecars) ─
"$PYTHON" - "$SOURCE" "$STAGE" "$WORK/materialise.list" - "$WORK" <<'PY' 2>&1 | tee -a "$LOG"
"""Snapshot live SQLite databases into the stage; verify the stage is complete."""
import os, shutil, sqlite3, stat, sys, time

HOME, STAGE, LIST, _dash, WORK = sys.argv[1:6]
BUDGET = int(os.environ.get('HOME_BACKUP_DB_BUDGET', '1800'))   # seconds per database


def budget_guard(what):
    """Abort one long sqlite statement rather than hang the whole nightly run."""
    deadline = time.monotonic() + BUDGET

    def check(*_a):
        if time.monotonic() > deadline:
            raise TimeoutError(f"{what} exceeded the {BUDGET}s budget")

    return check

raw = open(LIST, 'rb').read()
items = []
for chunk in raw.split(b'\0'):
    if chunk:
        kind, rel = chunk.decode().split('\t', 1)
        items.append((kind, rel))


def snapshot_sqlite(src, dst):
    size = os.path.getsize(src)
    if size < 4096:                                    # placeholder/empty file
        shutil.copy2(src, dst)
        return 'verbatim copy (empty)', True

    def vacuum():
        con = sqlite3.connect(src, timeout=60)
        try:
            # One consistent read pass. A writer in WAL mode cannot invalidate it, so
            # this always makes progress — unlike the backup API (see below).
            con.set_progress_handler(budget_guard('VACUUM INTO'), 50000)
            con.execute("VACUUM INTO ?", (dst,))
        finally:
            con.close()

    def backup_api():
        s = sqlite3.connect(src, timeout=60)
        d = sqlite3.connect(dst)
        try:
            with d:
                # The backup API restarts from page 1 every time the source is written,
                # so a continuously-written database loops here forever making no
                # progress. Keep it as the fallback, but only with the budget.
                s.backup(d, pages=4000, sleep=0.05,
                         progress=budget_guard('backup API'))
        finally:
            s.close(); d.close()

    errs = []
    for how, fn in (('VACUUM INTO', vacuum), ('backup API', backup_api)):
        t0 = time.monotonic()
        try:
            if os.path.exists(dst):
                os.remove(dst)
            fn()
            return f"{how} in {time.monotonic() - t0:.0f}s", True
        except Exception as exc:                        # noqa: BLE001 - fall through to raw copy
            errs.append(f"{how}: {exc.__class__.__name__}: {exc}")
    for suffix in ('', '-wal', '-shm', '-journal'):     # last resort: raw copy in full
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, dst + suffix)
    return 'RAW copy + wal/shm (' + '; '.join(errs) + ')', False


ok = warn = 0          # every materialise operation (db snapshots + sidecar copies)
db_ok = raw_ok = 0     # split out, so the headline number is databases, not operations
for kind, rel in sorted(items):
    src, dst = os.path.join(HOME, rel), os.path.join(STAGE, rel)
    os.makedirs(os.path.dirname(dst) or STAGE, exist_ok=True)
    try:
        if kind == 'sqlite':
            print(f".... snapshotting {rel} ({os.path.getsize(src)/1048576:.1f}MB)", flush=True)
            how, clean = snapshot_sqlite(src, dst)
            note = ''
            if clean:
                try:
                    con = sqlite3.connect(dst)
                    con.set_progress_handler(budget_guard('quick_check'), 50000)
                    note = ' quick_check=' + str(con.execute('PRAGMA quick_check').fetchone()[0])
                    con.close()
                except Exception as exc:                # noqa: BLE001
                    note = f' quick_check FAILED: {exc}' ; clean = False
                for suffix in ('-wal', '-shm', '-journal'):   # stale sidecars must not ship
                    if os.path.exists(dst + suffix):
                        os.remove(dst + suffix)
            print(f"{'OK  ' if clean else 'WARN'} snapshot {rel}  "
                  f"{os.path.getsize(src)/1048576:.1f}MB -> {how}{note}", flush=True)
            ok += clean
            warn += (not clean)
            db_ok += clean
        else:
            shutil.copy2(src, dst)
            print(f"OK   copy     {rel} (sqlite sidecar)", flush=True)
            ok += 1
            raw_ok += 1
    except Exception as exc:                            # noqa: BLE001
        print(f"FAIL materialise {rel}: {exc}", flush=True)
        warn += 1

# ── is the stage complete? ────────────────────────────────────────────────────
# include.list mixes three things: dirs (rsync needs them to build the tree),
# plain files/symlinks, and the live sqlite dbs + sidecars that rsync was told to
# skip and this script materialises instead. materialise.list holds those, in
# "kind\trelpath" form. Compare only non-directories, each against its own form.
want_srcs = {}
for entry in open(os.path.join(WORK, 'include.list'), 'rb').read().split(b'\0'):
    if entry:
        want_srcs[entry.decode('utf-8', 'surrogateescape')] = 'copy'
for entry in open(LIST, 'rb').read().split(b'\0'):
    if entry:
        kind, rel = entry.decode('utf-8', 'surrogateescape').split('\t', 1)
        want_srcs[rel] = kind

expect, missing, shipped_sidecars = 0, [], 0
for rel in sorted(want_srcs):
    sp = os.path.join(HOME, rel)
    if os.path.isdir(sp) and not os.path.islink(sp):    # dirs are not "entries"
        continue
    if want_srcs[rel] == 'raw':
        # materialise.list marks -wal/-shm files "raw": they only ship when their
        # database fell back to a raw copy. After a clean snapshot they are removed
        # on purpose, so they are not expected to be in the stage.
        shipped_sidecars += os.path.lexists(os.path.join(STAGE, rel))
        continue
    expect += 1
    if not os.path.lexists(os.path.join(STAGE, rel)):
        missing.append(rel)

print(f"stage check: expected {expect} non-dir entries, found {expect - len(missing)}"
      f"{'' if not missing else f' (missing {len(missing)})'}"
      f"{'' if not shipped_sidecars else f', {shipped_sidecars} sqlite sidecar(s) for raw copies'}")
if missing:
    for rel in missing[:15]:
        print(f"   not in stage: {rel}")
    if len(missing) > max(20, expect // 100):
        print("FAIL stage is materially incomplete — refusing to publish")
        sys.exit(1)
    print(f"WARN {len(missing)} file(s) vanished between scan and copy (deleted mid-run?)")
# Headline number = databases; `ok` also counts staged -wal/-shm sidecars, which made this
# line disagree with the manifest's database count. Report the split so the two agree.
print(f"sqlite snapshots: {db_ok} ok, {warn} degraded"
      f"{'' if not raw_ok else f' (+ {raw_ok} live -wal/-shm sidecar file(s))'}")
sys.exit(0)
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || die "database snapshot / stage verification failed"

else    # RESUMED = 1: reconstruct what the build would have reported
SCAN_FILES="$(find "$STAGE" -xdev -type f -printf . | wc -c)"
SCAN_SYMLINKS="$(find "$STAGE" -xdev -type l -printf . | wc -c)"
SCAN_DIRS="$(find "$STAGE" -xdev -type d -printf . | wc -c)"
SCAN_INCLUDED_BYTES="$(du -sb "$STAGE" 2>/dev/null | cut -f1)"
SCAN_ENTRIES="$SCAN_FILES"
SCAN_DBS="$(find "$STAGE" \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -type f -printf . 2>/dev/null | wc -c)"
SCAN_EXCLUDED="?"; SCAN_EXCLUDED_BYTES=0
CTR_SUMMARY="not re-captured (--resume: reuses the dumps already staged)"
log "resuming: reusing the stage at $STAGE ($(hsz "$SCAN_INCLUDED_BYTES"), $SCAN_FILES files) — not re-scanning $SOURCE"
fi

# ── provenance files that ride inside the archive ────────────────────────────
INFO="$STAGE/.backup-info"
mkdir -p "$INFO"
cp "$WORK/report.txt" "$INFO/EXCLUSIONS.txt"
cp "$WORK/rules.txt"  "$INFO/IGNORE-RULES.txt"

# every git repo we skipped, and whether skipping it would lose anything unreproducible
gv() { git -C "$1" "${@:2}" 2>/dev/null || true; }
{
  echo "Git repositories found under \$HOME — their .git directories are NOT in this archive."
  echo "Generated $(date '+%Y-%m-%d %H:%M:%S %Z') by home-backup.sh."
  echo
  echo "Skipping .git means: history, other branches and stashes are not preserved, while the"
  echo "checked-out files ARE (in the working tree). 'dirty' = modified/untracked paths,"
  echo "'unpushed' = commits that exist only on this machine."
  echo
  printf '%-38s %-9s %-8s %-8s %-7s %s\n' REPO HEAD BRANCH DIRTY UNPUSHED REMOTE
  while IFS= read -r g; do
    repo="${g%/.git}"
    rel="${repo#"$SOURCE"/}"
    head="$(gv "$repo" rev-parse --short HEAD)"; [ -n "$head" ] || head='(no commits)'
    branch="$(gv "$repo" rev-parse --abbrev-ref HEAD)"; [ -n "$branch" ] || branch='-'
    dirty="$(gv "$repo" status --porcelain | wc -l)"
    unpushed="$(gv "$repo" rev-list --count '@{u}..HEAD')"; [ -n "$unpushed" ] || unpushed='-'
    remote="$(gv "$repo" config --get remote.origin.url)"; [ -n "$remote" ] || remote='(no remote)'
    stashes="$(gv "$repo" stash list | wc -l)"
    printf '%-38s %-9s %-8s %-8s %-7s %s\n' "$rel" "$head" "$branch" "$dirty" "$unpushed" "$remote"
    if [ "$stashes" -gt 0 ]; then
      printf '    %s stash(es) NOT in this archive: %s\n' "$stashes" \
             "$(gv "$repo" stash list | tr '\n' ' ')"
    fi
    branches="$(gv "$repo" for-each-ref --format='%(refname:short)' refs/heads | tr '\n' ' ')"
    printf '    local branches: %s\n' "${branches:-none}"
  done < <(find "$SOURCE" -xdev -type d \
             \( -name node_modules -o -name .venv -o -name venv -o -name .cache \
                -o -name .vscode-server -o -name .npm -o -name .local -o -name .grok \
                -o -name .platformio \) -prune -o \
             -type d -name .git -prune -print 2>/dev/null | LC_ALL=C sort || true)
  echo
  echo "To restore a repo: git clone <remote> && cd <dir> && git checkout <HEAD>"
  echo "(the checked-out files in this archive are the working tree, not a commit)."
} > "$INFO/REPOS.txt"

# what the excluded toolchains were, so they can be reinstalled
{
  echo "Toolchains and versions as of $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "Their install trees (.venv, node_modules, .npm-global, .vscode-server, .cache,"
  echo ".platformio/packages) are excluded from the archive as re-downloadable."
  echo
  echo "--- interpreters ---"
  echo "python3: $(command -v python3) $("$PYTHON" -V 2>&1)"
  echo "node   : $(command -v node) $(node -v 2>/dev/null)"
  echo "npm    : $(command -v npm) $(npm -v 2>/dev/null)"
  echo "git    : $(command -v git) $(git --version 2>/dev/null)"
  echo
  echo "--- npm global packages (reinstall with: npm i -g <name>@<version>) ---"
  timeout 30 npm ls -g --depth=0 2>/dev/null || echo "(npm not available)"
  echo
  echo "--- python venvs / requirements files kept in this archive ---"
  find "$STAGE" -maxdepth 3 -type f \
       \( -name 'requirements*.txt' -o -name 'pyproject.toml' -o -name 'uv.lock' \
          -o -name 'poetry.lock' -o -name 'Pipfile.lock' -o -name 'package.json' \
          -o -name 'package-lock.json' -o -name 'pnpm-lock.yaml' \) -printf '  %P\n' 2>/dev/null \
    | head -60
} > "$INFO/TOOLS.txt" 2>/dev/null || true

# ── container data: docker volumes + live database dumps (outside $HOME) ─────
if [ "$RESUMED" = 0 ]; then
  docker_phase capture
fi
(cd "$STAGE" && find . -type f -printf '%P\n' | LC_ALL=C sort) > "$INFO/INCLUDED-FILES.txt"

{
  echo "home backup manifest"
  echo "===================="
  echo "created      : $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "host         : $(hostname)   user: $(id -un)   kernel: $(uname -r)"
  echo "archive      : home-backup-$STAMP.$EXT"
  echo "source       : $SOURCE   (all paths in this archive are relative to it)"
  echo "ignore file  : $IGNORE_FILE"
  echo
  echo "contents     : $SCAN_FILES files, $SCAN_DIRS dirs, $SCAN_SYMLINKS symlinks"
  echo "raw size     : $(hsz "$(du -sb "$STAGE" 2>/dev/null | cut -f1)")"
  echo "excluded     : $SCAN_EXCLUDED entries, $SCAN_EXCLUDED_BYTES bytes (see EXCLUSIONS.txt)"
  echo "sqlite       : $SCAN_DBS live database(s) snapshotted consistently (WAL folded in)"
  echo "containers   : $CTR_SUMMARY — docker volumes + db dumps, see DOCKER.txt"
  echo "compression  : $COMPRESS"
  echo
  echo "Restore (stop the cptr server and any app using these files first):"
  echo "    tar -xf home-backup-$STAMP.$EXT -C \$HOME"
  echo "One file only:"
  echo "    tar -xf home-backup-$STAMP.$EXT -C \$HOME .cptr/app.db"
  echo
  echo "Start here after restoring:"
  echo "    .backup-info/EXCLUSIONS.txt   what was left out and why"
  echo "    .backup-info/REPOS.txt        git repos whose .git was not archived (with HEAD)"
  echo "    .backup-info/TOOLS.txt        npm globals / versions to reinstall"
  echo "    .backup-info/DOCKER.txt       container volumes + database dumps (outside \$HOME)"
  echo "    .backup-info/IGNORE-RULES.txt every ignore rule in force, in order"
  echo "    .backup-info/INCLUDED-FILES.txt  exact list of files in this archive"
} > "$INFO/MANIFEST.txt"

# ── compress ─────────────────────────────────────────────────────────────────
TMP_OUT="$OUT/home-backup-$STAMP.$EXT.partial"
rm -f "$TMP_OUT"
log "compressing ($COMPRESS)..."
if [ -n "$TARCOMP" ]; then
  tar -C "$STAGE" -c --null -T <(find "$STAGE" -maxdepth 1 -mindepth 1 -printf '%P\0') \
      --use-compress-program="$TARCOMP" -f "$TMP_OUT" || die "tar failed"
else
  tar -C "$STAGE" --null -T <(find "$STAGE" -maxdepth 1 -mindepth 1 -printf '%P\0') \
      -cf "$TMP_OUT" || die "tar failed"
fi

# ── verify before publishing ─────────────────────────────────────────────────
case "$EXT" in
  tar.gz) gzip -t "$TMP_OUT" || die "gzip integrity check failed" ;;
  tar.xz) xz -t "$TMP_OUT" || die "xz integrity check failed" ;;
esac
tar -tf "$TMP_OUT" > "$WORK/listing.txt" || die "tar cannot read back its own archive"
ENTRIES="$(wc -l < "$WORK/listing.txt")"
[ "$ENTRIES" -gt 100 ] || die "archive looks empty ($ENTRIES entries) — not publishing"
grep -qx '\.backup-info/MANIFEST.txt' "$WORK/listing.txt" || die "MANIFEST missing — not publishing"

find "$STAGE" ! -type d -printf '%P\n' | LC_ALL=C sort > "$WORK/stage-files.txt"
grep -v '/$' "$WORK/listing.txt" | LC_ALL=C sort > "$WORK/archive-files.txt" || true
if ! diff -q "$WORK/stage-files.txt" "$WORK/archive-files.txt" >/dev/null; then
  diff "$WORK/stage-files.txt" "$WORK/archive-files.txt" | head -20 >> "$LOG"
  die "archive contents differ from the staged tree — not publishing"
fi
ENV_COUNT="$(grep -cE '(^|/)\.env' "$WORK/listing.txt" || true)"
log "verified: $ENTRIES entries, staged tree == archive ($(wc -l < "$WORK/stage-files.txt") non-dir entries), $ENV_COUNT .env file(s) included"
[ "${ENV_COUNT:-0}" -gt 0 ] || log "WARN no .env files found in $SOURCE at all — expected some"

LOCAL_SIZE="$(stat -c%s "$TMP_OUT")"
log "archive built: $(hsz "$LOCAL_SIZE")"

AVAIL_KB="$(df -Pk "$DEST" | awk 'NR==2 {print $4}')"
if [ "$AVAIL_KB" -lt $(( LOCAL_SIZE * 2 / 1024 )) ]; then
  log "WARNING: low space on $DEST — $(df -h "$DEST" | awk 'NR==2 {print $4}') free"
fi
if [ "$(df -Pk "$WORK" | awk 'NR==2 {print $4}')" -lt $(( SCAN_INCLUDED_BYTES / 1024 )) ]; then
  log "WARNING: $WORK has less free space than the staged tree needs next time"
fi

# ── publish (write-then-rename, verify on the far side) ──────────────────────
NAME="home-backup-$STAMP.$EXT"
LOCAL_SUM="$(sha256sum "$TMP_OUT" | cut -d' ' -f1)"
mv -f "$TMP_OUT" "$DEST/$NAME" || die "could not move archive into $DEST"
if [ "$(stat -c%s "$DEST/$NAME")" != "$LOCAL_SIZE" ]; then
  die "size mismatch after copy to $DEST — archive not trusted"
fi
log "sha256: $LOCAL_SUM"
printf '%s  %s\n' "$LOCAL_SUM" "$NAME" >> "$DEST/SHA256SUMS"
printf '%s\n' "$LOCAL_SUM  $NAME" > "$DEST/$NAME.sha256"

# ── retention ────────────────────────────────────────────────────────────────
if [ "$KEEP" -gt 0 ]; then
  mapfile -t OLD < <(ls -1t "$DEST"/home-backup-*.tar.gz "$DEST"/home-backup-*.tar.xz 2>/dev/null \
                     | tail -n +$((KEEP + 1)))
  for f in "${OLD[@]:-}"; do
    [ -n "$f" ] || continue
    if rm -f "$f" "$f.sha256"; then
      log "pruned old archive: $(basename "$f")"
    fi
  done
fi
if [ -f "$DEST/SHA256SUMS" ]; then
  SH_TMP="$(mktemp)"
  while read -r sum file; do
    [ -z "$file" ] || [ -e "$DEST/$file" ] || continue
    printf '%s  %s\n' "$sum" "$file"
  done < "$DEST/SHA256SUMS" > "$SH_TMP"
  mv -f "$SH_TMP" "$DEST/SHA256SUMS"
fi

{
  printf '[%s] %s  %s  %s raw  %s free\n' "$(date '+%Y-%m-%d %H:%M')" "$NAME" \
    "$(du -h "$DEST/$NAME" | cut -f1)" "$(hsz "$SCAN_INCLUDED_BYTES")" \
    "$(df -h "$DEST" | awk 'NR==2 {print $4}')"
} >> "$DEST/backup.log"

cat > "$DEST/RESTORE.md" <<EOF
# home backup — $DEST

Nightly whole-\$HOME snapshots written by \`~/home-backup.sh\` (cptr automation, 04:15 daily).

- \`home-backup-YYYYMMDD-HHMM.$EXT\` — one per night, newest first by mtime
- \`SHA256SUMS\` — \`cd $DEST && sha256sum -c SHA256SUMS\`
- \`backup.log\` — one line per run (name, compressed size, raw size, free space)
- \`<archive>.sha256\` — per-archive checksum

## Restore everything

    # stop cptr (and anything else using these files) first
    tar -xf home-backup-YYYYMMDD-HHMM.$EXT -C \$HOME

Paths are relative to \$HOME. Live SQLite databases in the archive were snapshotted
(VACUUM INTO / backup API), so the \`-wal\`/\`-shm\` sidecars are absent by design.

## Restore one thing

    tar -xf home-backup-YYYYMMDD-HHMM.$EXT -C \$HOME .cptr/app.db        # one file
    tar -xf home-backup-YYYYMMDD-HHMM.$EXT -C \$HOME AIjly/.env.prod     # one secret
    tar -tf  home-backup-YYYYMMDD-HHMM.$EXT | less                        # look around

## Container data (Docker)

Docker volumes are outside \$HOME and \`/var/lib/docker\` is root-only, so the archive
carries them itself:

- \`.backup-info/dbdumps/*.sql.gz\` — \`pg_dumpall\` of each running Postgres cluster
- \`.backup-info/dbdumps/*-dump.rdb\` — \`BGSAVE\` snapshot of each running Redis/FalkorDB
- \`.backup-info/docker/<volume>.tar.gz\` — every named volume, minus cache dirs inside it
- \`.backup-info/DOCKER.txt\` — what was captured, what was skipped and why, plus commands

    # unpack just the container data, somewhere temporary (\`/var/tmp\`, not \`/tmp\` —
    # on this machine /tmp is a small RAM-backed tmpfs)
    mkdir -p /var/tmp/hb && tar -xf home-backup-YYYYMMDD-HHMM.$EXT -C /var/tmp/hb .backup-info

    # put a volume back (creates the volume if it is missing)
    docker run --rm -i --entrypoint tar -v <volume>:/v alpine -xzf - -C /v \\
        < /var/tmp/hb/.backup-info/docker/<volume>.tar.gz

    # put a database cluster back
    gunzip -c /var/tmp/hb/.backup-info/dbdumps/<container>.sql.gz \\
        | docker exec -i -u postgres <container> psql -U <POSTGRES_USER> -d postgres

Skipped as re-downloadable: volumes named like a cache (\`*cache*\`, \`*-m2\`, \`*trivy*\`)
and \`cache\`/\`tmp\` dirs inside a volume. Change that with \`HOME_BACKUP_VOLUMES\`,
\`HOME_BACKUP_VOLUMES_SKIP\`, \`HOME_BACKUP_VOLUMES_EXCLUDE\` (or \`HOME_BACKUP_DOCKER=0\`
to ignore containers entirely).

## What is not in the archive

\`.git\`, \`node_modules\`, \`.venv\`/\`venv\`, \`__pycache__\`, pip/npm/yarn/cargo caches,
\`~/.cache\`, \`~/.vscode-server\`, \`~/.npm-global\`, \`~/.platformio/packages\`, OS droppings —
everything re-downloadable. Inside each archive:

- \`.backup-info/EXCLUSIONS.txt\` — what was excluded, why, how big, and the biggest items included
- \`.backup-info/REPOS.txt\` — every git repo skipped, with HEAD/branch/stash/unpushed counts
- \`.backup-info/TOOLS.txt\` — npm globals and interpreter versions to reinstall
- \`.backup-info/IGNORE-RULES.txt\` — the exact rule list in force, in order
- \`.backup-info/INCLUDED-FILES.txt\` — every file in the archive
- \`.backup-info/dbdumps/\` + \`.backup-info/docker/\` — container data (see "Container data" above)
- \`.backup-info/MANIFEST.txt\` — summary, sizes, restore hints

## Changing what is captured

Edit \`~/.backupignore\` (gitignore rules; \`!pattern\` re-includes). Preview without writing
anything: \`~/home-backup.sh --dry-run\`. Ask why a path is in or out:
\`~/home-backup.sh --why node_modules\`. Knobs: \`HOME_BACKUP_KEEP\`, \`HOME_BACKUP_COMPRESS\`,
\`HOME_BACKUP_DEST\`.
EOF

rm -rf "$STAGE"
log "=== done: $DEST/$NAME ($(du -h "$DEST/$NAME" | cut -f1)), keeping $KEEP ==="
