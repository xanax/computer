"""Per-workspace dwell-time estimate from ui_events.

Method: order events by true event time (ts), then attribute each gap to the
workspace the event *before* it was tagged with — i.e. the workspace the user
was in during that gap. Gaps are capped so idle/away time isn't counted.

Caveats are printed with the output on purpose; this is an estimate.
"""
import sqlite3
import datetime as dt
import collections

DB = "/home/brendan/.cptr/app.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

rows = list(c.execute(
    "select ts, workspace, kind, label, session_id from ui_events "
    "where ts > 1e12 order by ts"
))
print("events with real ts: %d" % len(rows))
if rows:
    print("range: %s -> %s" % (
        dt.datetime.fromtimestamp(rows[0]["ts"] / 1000).strftime("%m-%d %H:%M"),
        dt.datetime.fromtimestamp(rows[-1]["ts"] / 1000).strftime("%m-%d %H:%M")))

n_all = c.execute("select count(*) from ui_events").fetchone()[0]
null_ws = c.execute(
    "select count(*) from ui_events where workspace is null and ts > 1e12").fetchone()[0]
print("all rows: %d ; recent rows with NULL workspace: %d (%.1f%%)" % (
    n_all, null_ws, 100.0 * null_ws / max(1, len(rows))))


def dwell(cap_s, same_session=False):
    """Sum gaps per workspace, capped at cap_s seconds."""
    tot = collections.defaultdict(float)
    for a, b in zip(rows, rows[1:]):
        gap = (b["ts"] - a["ts"]) / 1000.0
        if gap <= 0 or gap > cap_s:
            continue
        if same_session and a["session_id"] != b["session_id"]:
            continue
        ws = a["workspace"] or "(unknown)"
        tot[ws] += gap
    return tot


for cap in (60, 120, 300):
    t = dwell(cap)
    total = sum(t.values())
    print("\n=== cap %ds  -> tracked %s (%.1f h) ===" % (
        cap, str(dt.timedelta(seconds=int(total))), total / 3600))
    for ws, secs in sorted(t.items(), key=lambda kv: -kv[1])[:14]:
        print("   %6.1f%%  %8.1f min   %s" % (100.0 * secs / total, secs / 60, ws))

print("\n=== cap 120s, gaps must stay within one page session ===")
t = dwell(120, same_session=True)
total = sum(t.values())
print("tracked %s (%.1f h)" % (str(dt.timedelta(seconds=int(total))), total / 3600))
for ws, secs in sorted(t.items(), key=lambda kv: -kv[1])[:14]:
    print("   %6.1f%%  %8.1f min   %s" % (100.0 * secs / total, secs / 60, ws))

print("\n=== event density per workspace (attribution bias check) ===")
cnt = collections.Counter(r["workspace"] or "(unknown)" for r in rows)
for ws, n in cnt.most_common(8):
    print("   %6d events  %s" % (n, ws))

print("\n=== how much of the window is 'silence' (no events for >2 min)? ===")
silent = 0.0
for a, b in zip(rows, rows[1:]):
    gap = (b["ts"] - a["ts"]) / 1000.0
    if gap > 120:
        silent += gap
print("   %.1f h of gaps longer than 2 min (cannot be attributed)" % (silent / 3600))
