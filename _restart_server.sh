#!/bin/bash
# One-shot restarter for the cptr server.
#
# The server is the parent of the agent's shell, so killing it ends the live chat
# turn. This script therefore sleeps first, giving the final chat message time to
# render, then detaches itself from the dying process tree and does the work.

cd /home/brendan/computer || exit 1

REPORT=/home/brendan/computer/restart-report.log
PIDFILE=/home/brendan/computer/cptr-pid.txt
STARTLOG=/home/brendan/computer/cptr-start.log
DELAY=${1:-20}

echo "=== restart requested $(date -Is), waiting ${DELAY}s ===" >>"$REPORT"
sleep "$DELAY"

OLD=$(tr -dc '0-9' <"$PIDFILE" 2>/dev/null)
echo "old pid: ${OLD:-<none>}" >>"$REPORT"

if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
	kill "$OLD" 2>>"$REPORT"
	for _ in $(seq 1 40); do
		kill -0 "$OLD" 2>/dev/null || break
		sleep 0.5
	done
	if kill -0 "$OLD" 2>/dev/null; then
		echo "graceful stop timed out, sending SIGKILL" >>"$REPORT"
		kill -9 "$OLD" 2>>"$REPORT"
		sleep 1
	fi
	echo "old server stopped" >>"$REPORT"
else
	echo "old server not running" >>"$REPORT"
fi

# The old process holds :4200 until the socket is released.
for _ in $(seq 1 20); do
	if ! ss -ltn 2>/dev/null | grep -q ':4200'; then break; fi
	sleep 0.5
done
echo "port 4200 listeners: $(ss -ltn 2>/dev/null | grep -c ':4200')" >>"$REPORT"

cd /home/brendan/computer || exit 1
setsid nohup .venv/bin/python -m cptr.cli run --host 0.0.0.0 --port 4200 --headless >>"$STARTLOG" 2>&1 &
NEW=$!
echo "$NEW" >"$PIDFILE"
echo "new pid: $NEW" >>"$REPORT"

for i in $(seq 1 120); do
	CODE=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:4200/api/health 2>/dev/null)
	if [ "$CODE" = "200" ]; then
		echo "health 200 after ${i}s" >>"$REPORT"
		echo "=== restart complete $(date -Is) ===" >>"$REPORT"
		exit 0
	fi
	sleep 1
done

echo "health check FAILED after 120s (last code: ${CODE:-none})" >>"$REPORT"
echo "=== restart incomplete $(date -Is) ===" >>"$REPORT"
exit 1
