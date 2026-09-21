#!/usr/bin/env bash
#
# cptr-backup.sh — nightly snapshot of all cptr agent data.
# Source of truth: ~/computer/scripts/cptr-backup.sh   (install: cp to ~/cptr-backup.sh)
#
# What it backs up (paths stored relative to $HOME, so extract-in-$HOME restores):
#   every  ~/**/.cptr  and  ~/**/.cptr-*   (data dirs + per-workspace agent state)
#   ~/lane-logs                 lane server logs
#   ~/.cache/cptr-chrome        harness browser profile
#   ~/cptr-lanes.sh             lane launcher
#   + anything listed in ~/.cptr-backup-includes  (one path per line,
#     relative to $HOME or absolute; a line starting with ! is a tar --exclude)
#
# SQLite databases are snapshotted with VACUUM INTO (backup-API fallback) instead
# of being copied raw, so a live/WAL database (~/.cptr/app.db) is captured
# consistently and without its -wal/-shm siblings.
#
# Output: /mnt/e/cptr_backup/cptr-backup-YYYYMMDD-HHMM.tar.gz
#
# Env knobs:
#   CPTR_BACKUP_DEST           default /mnt/e/cptr_backup
#   CPTR_BACKUP_KEEP           how many nightly archives to retain (default 14, 0 = all)
#   CPTR_BACKUP_INCLUDE_CACHE  1 = also archive regenerable .cptr/cache dirs (TTS audio)
#
# Scheduled (user crontab):  23 3 * * *
#
set -euo pipefail

DEST="${CPTR_BACKUP_DEST:-/mnt/e/cptr_backup}"
KEEP="${CPTR_BACKUP_KEEP:-14}"
INCLUDE_CACHE="${CPTR_BACKUP_INCLUDE_CACHE:-0}"
SRC_HOME="${HOME}"
STAMP="$(date +%Y%m%d-%H%M)"
NAME="cptr-backup-${STAMP}.tar.gz"

# Working area on the big root fs (never on /mnt/e, never on the small tmpfs)
WORK="${SRC_HOME}/.cache/cptr-backup"
STAGE="${WORK}/stage"
OUT="${WORK}/out"
LOCKFILE="${WORK}/run.lock"
LOG="${WORK}/backup.log"
INCLUDES="${SRC_HOME}/.cptr-backup-includes"

mkdir -p "$WORK" "$OUT"

# ── logging ───────────────────────────────────────────────────────────────────
rotate_log() {  # keep the local log from growing forever
  if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 5242880 ]; then
    mv -f "$LOG" "${LOG}.1"
  fi
}
log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
die() { log "FATAL: $*"; exit 1; }

# ── single instance ───────────────────────────────────────────────────────────
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  log "another backup run holds the lock — exiting"
  exit 0
fi

rotate_log
# clear staging left behind by a crashed run — otherwise the source scan below finds the
# .cptr copies inside it and starts backing up its own working area.
rm -rf "$STAGE"
log "=== cptr backup start (pid $$) ==="

# ── destination must exist (E: lives on drvfs and can be missing) ─────────────
if [ ! -d /mnt/e ]; then
  die "/mnt/e is not mounted — cannot back up. (In WSL, start the E: drive / restart the distro.)"
fi
mkdir -p "$DEST" || die "cannot create $DEST"
[ -w "$DEST" ] || die "$DEST is not writable"

# ── collect sources ──────────────────────────────────────────────────────────
mapfile -t TREES < <(
  {
    find "$SRC_HOME" -xdev \
      \( -name node_modules -o -name .git -o -name .venv -o -name venv \
         -o -name .Trash -o -name '.Trash-*' -o -path "$WORK" \) -prune -o \
      \( -name '.cptr' -o -name '.cptr-*' \) -print 2>/dev/null || true
    # NB: the `|| true` matters — find exits 1 on unreadable dirs, and under `set -e`
    # that would abort this block before the extras below are printed.
    printf '%s\n' "$SRC_HOME/lane-logs" "$SRC_HOME/.cache/cptr-chrome" "$SRC_HOME/cptr-lanes.sh"
    if [ -f "$INCLUDES" ]; then
      grep -v '^[[:space:]]*\(#\|!\|$\)' "$INCLUDES" 2>/dev/null | sed 's/[[:space:]]*$//' || true
    fi
  } | sort -u
)

TREES_EXIST=()
for t in "${TREES[@]}"; do
  [ -e "$t" ] || { log "skip (missing): $t"; continue; }
  case "$t" in "$WORK"/*) log "skip (own work dir): $t"; continue ;; esac
  case "$t" in
    /*) TREES_EXIST+=("${t#"$SRC_HOME"/}") ;;
    *)  TREES_EXIST+=("$t") ;;
  esac
done
[ "${#TREES_EXIST[@]}" -gt 0 ] || die "no source trees found — refusing to write an empty backup"
log "sources: ${#TREES_EXIST[@]} trees"

# ── excludes ─────────────────────────────────────────────────────────────────
EXCLUDES=(
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm'   # DBs are snapshotted properly
  --exclude '*.sqlite' --exclude '*.sqlite-wal' --exclude '*.sqlite-shm'
  --exclude 'SingletonLock' --exclude 'SingletonCookie' --exclude 'SingletonSocket'
  --exclude 'Singleton*'
  --exclude 'GPUCache/' --exclude 'Code Cache/' --exclude 'Cache/' --exclude 'GrShaderCache/'
  --exclude '*.tmp' --exclude '*.partial' --exclude '.nfs*'
)
if [ "$INCLUDE_CACHE" != "1" ]; then
  EXCLUDES+=(--exclude 'cache/')   # cptr caches (TTS audio etc.) are regenerable
  log "note: .cptr/cache dirs excluded (set CPTR_BACKUP_INCLUDE_CACHE=1 to include)"
fi
if [ -f "$INCLUDES" ]; then
  while IFS= read -r line; do
    case "$line" in
      !*) EXCLUDES+=(--exclude "${line#!}") ;;
    esac
  done < "$INCLUDES"
fi

# ── stage a filesystem copy ──────────────────────────────────────────────────
rm -rf "$STAGE"
mkdir -p "$STAGE"
for rel in "${TREES_EXIST[@]}"; do
  parent="$(dirname "$rel")"
  [ "$parent" = "." ] || mkdir -p "$STAGE/$parent"
  rsync -a --delete-excluded "${EXCLUDES[@]}" "${SRC_HOME}/${rel}" "$STAGE/${parent}/" \
    || die "rsync failed for ${rel}"
done
log "staged: $(du -sh "$STAGE" | cut -f1)"

# ── consistent SQLite snapshots (replaces the raw .db files skipped above) ────
DB_LOG="$(mktemp)"
snapshot_db() {  # $1 = db, $2 = destination file
  python3 - "$1" "$2" <<'PY' >>"$DB_LOG" 2>&1
import os, shutil, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(dst), exist_ok=True)
size = os.path.getsize(src)

def result(how, ok=True, note=''):
    print(f"{'OK  ' if ok else 'WARN'} {os.path.relpath(src, os.path.expanduser('~'))}"
          f"  {size/1048576:.1f}MB -> {how}{('  ' + note) if note else ''}")

if size < 4096:                       # placeholder / empty file (e.g. ~/.cptr/cptr.db)
    shutil.copy2(src, dst); result('verbatim copy', note='empty/unsized file'); raise SystemExit(0)

try:                                  # preferred: compact, single-file, no WAL
    con = sqlite3.connect(src, timeout=60)
    try:
        con.execute("VACUUM INTO ?", (dst,))
    finally:
        con.close()
    result('VACUUM INTO')
except Exception as e:
    try:                              # fallback: online backup API
        src_con = sqlite3.connect(src, timeout=60)
        dst_con = sqlite3.connect(dst)
        with dst_con:
            src_con.backup(dst_con)
        src_con.close(); dst_con.close()
        result('backup API', note=f'vacuum failed: {e.__class__.__name__}')
    except Exception as e2:           # last resort: raw copy with its WAL siblings
        for suffix in ('', '-wal', '-shm'):
            if os.path.exists(src + suffix):
                shutil.copy2(src + suffix, dst + suffix)
        result('RAW copy + wal/shm', ok=False, note=f'{e2}')

try:                                  # sanity check the snapshot we just wrote
    con = sqlite3.connect(dst)
    print('     quick_check:', con.execute('PRAGMA quick_check').fetchone()[0])
    con.close()
except Exception as e:
    print('     quick_check failed:', e)
PY
}

DB_COUNT=0
while IFS= read -r db; do
  rel="${db#"$SRC_HOME"/}"
  # respect the same exclusions rsync applied, so we never resurrect a skipped path
  case "$rel" in
    */cache/*|cache/*|*/node_modules/*|*/.git/*|*/.venv/*)
      [ "$INCLUDE_CACHE" = 1 ] || { log "skip db in excluded dir: $rel"; continue; } ;;
  esac
  snapshot_db "$db" "$STAGE/$rel"
  DB_COUNT=$((DB_COUNT + 1))
done < <(
  for rel in "${TREES_EXIST[@]}"; do
    find "${SRC_HOME}/${rel}" \
      \( -name node_modules -o -name .git -o -name .venv \) -prune -o \
      -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -print 2>/dev/null || true
  done | sort -u
)
log "snapshotted ${DB_COUNT} sqlite database(s):"
while IFS= read -r l; do log "  $l"; done < "$DB_LOG"
rm -f "$DB_LOG"

# ── manifest ─────────────────────────────────────────────────────────────────
{
  echo "cptr backup manifest"
  echo "===================="
  echo "created      : $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "host         : $(hostname)   user: $(id -un)   kernel: $(uname -r)"
  echo "archive      : $NAME"
  echo "staged from  : $SRC_HOME"
  echo "cptr HEAD    : $(git -C "$SRC_HOME/computer" log -1 --format='%h %ci %s' 2>/dev/null || echo n/a)"
  echo "cptr status  : $(git -C "$SRC_HOME/computer" status --porcelain 2>/dev/null | wc -l) uncommitted path(s) in ~/computer (source tree NOT in this archive)"
  echo "cache        : $([ "$INCLUDE_CACHE" = 1 ] && echo included || echo 'excluded (.cptr/cache — regenerable)')"
  echo
  echo "trees included (relative to \$HOME):"
  for rel in "${TREES_EXIST[@]}"; do
    if [ -e "$STAGE/$rel" ]; then
      printf '  %-48s %6s  %s files\n' "$rel" "$(du -sh "$STAGE/$rel" | cut -f1)" \
        "$(find "$STAGE/$rel" -type f 2>/dev/null | wc -l || true)"
    fi
  done
  echo
  echo "NOT included: source checkouts (git repos with remotes: ~/computer, ~/AIjly, ...)"
  echo "              other agents' state (~/.grok, ~/.copilot), cptr caches, node_modules."
  echo "To add paths, list them in $INCLUDES (one per line; '!pattern' to exclude)."
  echo
  echo "Restore: tar -xzf $NAME -C \$HOME   (do it with the cptr server stopped)"
} > "$STAGE/MANIFEST.txt"

# ── compress ─────────────────────────────────────────────────────────────────
TMP_OUT="$OUT/${NAME}.partial"
rm -f "$TMP_OUT"
log "compressing..."
# Store clean relative paths (no "./" prefix) so `tar -xzf x.tar.gz -C ~ .cptr/app.db` works.
tar -C "$STAGE" --null -T <(find "$STAGE" -maxdepth 1 -mindepth 1 -printf '%P\0') \
  -czf "$TMP_OUT" || die "tar/gzip failed"
gzip -t "$TMP_OUT" || die "gzip integrity check failed on the new archive"
TAR_LIST="$(tar -tzf "$TMP_OUT")"
ENTRIES="$(printf '%s\n' "$TAR_LIST" | wc -l)"
[ "$ENTRIES" -gt 100 ] || die "archive looks empty (${ENTRIES} entries) — not publishing"
# NB: match without a pipeline — `printf | grep -q` trips pipefail via SIGPIPE.
has_entry() { case $'\n'"$TAR_LIST"$'\n' in *$'\n'"$1"$'\n'*) return 0 ;; *) return 1 ;; esac; }
has_entry 'MANIFEST.txt' || die "MANIFEST.txt missing from archive — not publishing"
has_entry '.cptr/app.db' || log "WARN: .cptr/app.db not in archive"
LOCAL_SIZE="$(stat -c%s "$TMP_OUT")"
log "archive contents: ${ENTRIES} entries"
log "archive built: $(du -h "$TMP_OUT" | cut -f1)"

# ── disk-space sanity (need room for a few more archives) ────────────────────
AVAIL_KB="$(df -Pk "$DEST" | awk 'NR==2 {print $4}')"
if [ "$AVAIL_KB" -lt $(( LOCAL_SIZE * 3 / 1024 )) ]; then
  log "WARNING: low space on $DEST — $(df -h "$DEST" | awk 'NR==2 {print $4}') free"
fi

# ── publish (write-then-rename) + verify the copy on the backup drive ────────
LOCAL_SUM="$(sha256sum "$TMP_OUT" | cut -d' ' -f1)"
mv -f "$TMP_OUT" "$DEST/$NAME" || die "could not move archive into $DEST"
if [ "$(stat -c%s "$DEST/$NAME")" != "$LOCAL_SIZE" ]; then
  die "size mismatch after copy to $DEST — archive not trusted"
fi
log "sha256: $LOCAL_SUM"
printf '%s  %s\n' "$LOCAL_SUM" "$NAME" >> "$DEST/SHA256SUMS"

# ── retention ────────────────────────────────────────────────────────────────
if [ "$KEEP" -gt 0 ]; then
  mapfile -t OLD < <(ls -1t "$DEST"/cptr-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)))
  for f in "${OLD[@]:-}"; do
    [ -n "$f" ] || continue
    rm -f "$f" && log "pruned old archive: $(basename "$f")"
  done
fi
# keep SHA256SUMS in step with what is actually there
if [ -f "$DEST/SHA256SUMS" ]; then
  SH_TMP="$(mktemp)"
  while read -r sum file; do
    [ -e "$DEST/$file" ] && printf '%s  %s\n' "$sum" "$file"
  done < "$DEST/SHA256SUMS" > "$SH_TMP"
  mv -f "$SH_TMP" "$DEST/SHA256SUMS"
fi

# ── human summary in the backup folder ───────────────────────────────────────
{
  printf '[%s] %s  %s  %s free\n' "$(date '+%Y-%m-%d %H:%M')" "$NAME" \
    "$(du -h "$DEST/$NAME" | cut -f1)" "$(df -h "$DEST" | awk 'NR==2 {print $4}')"
} >> "$DEST/backup.log"

cat > "$DEST/RESTORE.md" <<EOF
# cptr backup — /mnt/e/cptr_backup

Nightly snapshots written by \`~/cptr-backup.sh\` (cron: 23 3 * * *).

- \`cptr-backup-YYYYMMDD-HHMM.tar.gz\` — one per night, newest first by mtime
- \`SHA256SUMS\` — verify with \`cd /mnt/e/cptr_backup && sha256sum -c SHA256SUMS\`
- \`backup.log\` — one line per run (name, size, free space)
- \`MANIFEST.txt\` inside each archive — what was included, cptr git HEAD at the time

## Restore everything

    # stop cptr first, so nothing writes while you unpack
    tar -xzf cptr-backup-YYYYMMDD-HHMM.tar.gz -C \$HOME

Paths in the archive are relative to \$HOME, so this restores
\`~/.cptr\`, every workspace's \`.cptr\`, \`~/lane-logs\`, \`~/.cache/cptr-chrome\`.
SQLite databases were snapshotted consistently (WAL content folded in); the
\`-wal\`/\`-shm\` sidecars are intentionally absent.

## Restore one thing only

    tar -xzf cptr-backup-YYYYMMDD-HHMM.tar.gz -C \$HOME .cptr/app.db
    tar -xzf cptr-backup-YYYYMMDD-HHMM.tar.gz -C \$HOME computer/.cptr

## Changing what is captured

Edit \`~/.cptr-backup-includes\` (one path per line, relative to \$HOME or absolute;
a line starting with \`!\` is a tar exclude pattern), or set
\`CPTR_BACKUP_INCLUDE_CACHE=1\` to include the regenerable \`.cptr/cache\` dirs.

Source checkouts (~/computer, ~/AIjly, ...) are deliberately not archived — they
are git repos with remotes. Add them to the includes file if you want them anyway.
EOF

rm -rf "$STAGE"
log "=== done: $DEST/$NAME ($(du -h "$DEST/$NAME" | cut -f1)), kept $KEEP ==="
