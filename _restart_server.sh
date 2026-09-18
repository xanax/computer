#!/bin/bash
# One-shot restarter for the cptr server.
#
# The server is the parent of the agent's shell, so killing it ends the live chat
# turn. This script therefore sleeps first, giving the final chat message time to
# render, then detaches itself from the dying process tree and does the work.
#
# Usage: bash _restart_server.sh [delay_seconds]   (default 20)
#
# Results land in restart-report.log; cptr-start.log holds the server's own output.

cd /home/brendan/computer || exit 1

REPORT=/home/brendan/computer/restart-report.log
PIDFILE=/home/brendan/computer/cptr-pid.txt
STARTLOG=/home/brendan/computer/cptr-start.log
PORT=4200
DELAY=${1:-20}

echo "=== restart requested $(date -Is), waiting ${DELAY}s ===" >>"$REPORT"
sleep "$DELAY"

pidfile_pid() { tr -dc '0-9' <"$PIDFILE" 2>/dev/null; }
port_pids() { ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u; }

# Kill only processes that provably hold the port, plus the pid file's pid if it
# is still alive. The pid file went stale once (it held a pid from an earlier
# restart), and pgrep-style matching is unreliable here: it also matches shells
# whose command line merely mentions the server, and other cptr instances that
# are running on purpose (e.g. a dev server on another port). So pgrep is logged
# for diagnostics only — :4200 is the authoritative signal.
OLD_FILE=$(pidfile_pid)
if [ -n "$OLD_FILE" ] && ! kill -0 "$OLD_FILE" 2>/dev/null; then OLD_FILE=""; fi
OLD_LIVE=$(pgrep -f '[p]ython[0-9.]* -m cptr\.cli run' 2>/dev/null | sort -u)
OLD_PORT=$(port_pids)
echo "pidfile alive: ${OLD_FILE:-<none>} | pgrep (info only): $(echo $OLD_LIVE) | listening: $(echo $OLD_PORT)" >>"$REPORT"
OLD=$(printf '%s\n%s\n' "$OLD_FILE" "$OLD_PORT" | grep -E '^[0-9]+$' | sort -u)

for PID in $OLD; do
	kill -0 "$PID" 2>/dev/null || continue
	kill "$PID" 2>>"$REPORT" && echo "signalled pid $PID" >>"$REPORT"
	for _ in $(seq 1 40); do
		kill -0 "$PID" 2>/dev/null || break
		sleep 0.5
	done
	if kill -0 "$PID" 2>/dev/null; then
		echo "graceful stop of $PID timed out, sending SIGKILL" >>"$REPORT"
		kill -9 "$PID" 2>>"$REPORT"
	fi
done
[ -n "$OLD" ] || echo "old server not running (nothing holds :$PORT)" >>"$REPORT"

# The old process holds :4200 until the socket is released.
for _ in $(seq 1 20); do
	[ -z "$(port_pids)" ] && break
	sleep 0.5
done
STILL=$(port_pids)
echo "port $PORT listeners before start: ${STILL:-none}" >>"$REPORT"
if [ -n "$STILL" ]; then
	echo "ABORT: pid(s) $STILL still hold :$PORT — not starting a second server" >>"$REPORT"
	echo "=== restart incomplete $(date -Is) ===" >>"$REPORT"
	exit 1
fi

cd /home/brendan/computer || exit 1
setsid nohup .venv/bin/python -m cptr.cli run --host 0.0.0.0 --port "$PORT" --headless >>"$STARTLOG" 2>&1 &
NEW=$!
echo "$NEW" >"$PIDFILE"
echo "new pid: $NEW" >>"$REPORT"

for i in $(seq 1 120); do
	CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/api/health" 2>/dev/null)
	if [ "$CODE" = "200" ]; then
		echo "health 200 after ${i}s" >>"$REPORT"
		SERVED=$(port_pids)
		echo "serving pid(s): ${SERVED:-unknown}" >>"$REPORT"
		if [ -n "$SERVED" ] && ! echo "$SERVED" | grep -qx "$NEW"; then
			echo "WARNING: health is answered by $SERVED, not the new pid $NEW" >>"$REPORT"
		fi
		# Smoke test: the download endpoint must honour ?filename= (post-2026-09 code).
		CHECK=$(mktemp /tmp/restart-check-XXXXXX.pdf)
		printf '%%PDF-1.4 restart check\n' >"$CHECK"
		TOKEN=$(.venv/bin/python - 2>/dev/null <<'PY'
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, "/home/brendan/computer")
from cptr.utils.config import create_token
row = sqlite3.connect(str(Path.home() / ".cptr" / "app.db")).execute(
    "select a.user_id, a.username, u.role from auths a join users u on u.id = a.user_id limit 1"
).fetchone()
print(create_token(row[0], row[1], role=row[2]))
PY
)
		[ -n "$TOKEN" ] || echo "WARNING: could not mint a session token for the smoke test" >>"$REPORT"
		QP=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$CHECK")
		for SUFFIX in "&filename=Restart%20Check.pdf" ""; do
			curl -s -D - -o /dev/null -b "cptr_session=$TOKEN" \
				"http://localhost:$PORT/api/workspace/files/download?path=$QP$SUFFIX" \
				| grep -iE '^HTTP/|^content-disposition' | sed 's/^/  /' >>"$REPORT"
		done
		rm -f "$CHECK"
		echo "=== restart complete $(date -Is) ===" >>"$REPORT"
		exit 0
	fi
	sleep 1
done

echo "health check FAILED after 120s (last code: ${CODE:-none})" >>"$REPORT"
echo "=== restart incomplete $(date -Is) ===" >>"$REPORT"
exit 1
