#!/usr/bin/env bash
# Regression guard for "per-workspace chat lists persist until closed".
#
#   A chat keeps its sidebar row for as long as it has not been closed.
#   Reading it does not remove it. Closing it does.
#
# Usage:
#   .venv/bin/python .cptr/harness/mint-cookie.py --user xanax
#   bash notes/kb-evidence/verify-sidebar-persist-until-closed.sh [port]
#
# Two traps this script exists to survive, both of which produce a
# "0 rows everywhere" result that reads exactly like a broken feature:
#
#   1. Mint for the user who OWNS the chats, not the one you happen to be
#      signed in as. /api/chats returns {"chats":[]} for a valid session whose
#      user_id owns nothing. On this box: xanax owns all 399 chats.
#   2. /api/chats validates limit<=200. Asking for 500 is a 422 whose body has
#      no "chats" key -- which the probes read as "zero chats", not as an error.
#      Page until has_more is false instead.
#
# Probe 3 (close removes a row) MUTATES REAL DATA. It closes one chat and must
# be run deliberately; it does not restore it.

set -uo pipefail
PORT="${1:-4200}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EV="$ROOT/notes/kb-evidence"

probe() {
  # probe <file> <label>; prints the harness output, returns non-zero on fail
  node "$ROOT/.cptr/harness/cdp.mjs" \
    --url "http://127.0.0.1:$PORT/" \
    --js "$EV/$1" --wait 28000 --size 1400x1100
}

echo "=== 1. every open chat has a row (read chats included) ==="
OUT=$(probe probe-sidebar-rows-persist.js) || { echo "FAIL: probe did not run"; echo "$OUT" | tail -20; exit 1; }
echo "$OUT" | head -14
R1=$(echo "$OUT" | python3 -c "import json,sys; t=sys.stdin.read(); d=json.loads(t[t.find('{'):]); print('PASS' if d['pass'] else 'FAIL')")

echo
echo "=== 2. no per-workspace 'Show more' survives ==="
R2=$(echo "$OUT" | python3 -c "
import json,sys
t=sys.stdin.read(); d=json.loads(t[t.find('{'):])
n=d['perWorkspaceShowMoreTotal']
print('PASS' if n==0 else f'FAIL ({n} left)')")

echo
echo "=== 3. the all-workspaces tail toggle is preserved ==="
R3=$(echo "$OUT" | python3 -c "
import json,sys
t=sys.stdin.read(); d=json.loads(t[t.find('{'):])
print('PASS' if d['tailToggle'] else 'FAIL (no tail toggle)')")

echo
echo "1: $R1   2: $R2   3: $R3"
if [ "$R1" = PASS ] && [ "$R2" = PASS ] && [ "$R3" = PASS ]; then
  echo
  echo "PASS"
  exit 0
fi
echo
echo "FAIL"
exit 1