import sqlite3
import datetime as dt
con = sqlite3.connect("/home/brendan/.cptr/app.db"); con.row_factory = sqlite3.Row
c = con.cursor()

# Only rows with a real event timestamp (ts = epoch ms).
print("rows with real ts: %d of %d" % (
    c.execute("select count(*) from ui_events where ts > 1e12").fetchone()[0],
    c.execute("select count(*) from ui_events").fetchone()[0]))

print("\n== per-hour using TRUE event time (ts) ==")
for x in c.execute("""select strftime('%H',ts/1000,'unixepoch','localtime') h,
                      count(*) n, round(sum(duration_ms)/1000.0,1) secs
                      from ui_events where kind='long_task' and ts > 1e12
                      group by h order by h"""):
    print("   %s:00  %4d long tasks  %7.1fs blocked" % (x["h"], x["n"], x["secs"]))

tot = c.execute("select sum(duration_ms) from ui_events where kind='long_task' and ts>1e12").fetchone()[0]
n = c.execute("select count(*) from ui_events where kind='long_task' and ts>1e12").fetchone()[0]
print("\n   TOTAL (post-fix rows only): %.0fs blocked across %d long tasks" % (tot / 1000, n))

# Real span of use = sum of gaps under 5 min between consecutive events (active time)
ts_list = [r[0] for r in c.execute("select ts from ui_events where ts>1e12 order by ts") if r[0]]
active = 0.0
for a, b in zip(ts_list, ts_list[1:]):
    gap = (b - a) / 1000.0
    if 0 <= gap <= 300:
        active += gap
print("   ACTIVE window (gaps<5min summed): %.0fs = %.1f min" % (active, active / 60))
print("   => main thread blocked %.0f%% of active time" % (100.0 * tot / 1000 / active))

print("\n== long tasks >1s, with true time ==")
for x in c.execute("""select duration_ms, strftime('%H:%M:%S',ts/1000,'unixepoch','localtime') t,
                      workspace from ui_events where kind='long_task' and duration_ms>1000 and ts>1e12
                      order by duration_ms desc limit 10"""):
    print("   %7.0fms  %s  %s" % (x["duration_ms"], x["t"], x["workspace"]))

print("\n== mount bursts using TRUE ts (one workspace load) ==")
for x in c.execute("""select round(ts/1000.0,0) s, count(*) n, group_concat(label) labels,
                      round(sum(duration_ms),0) total
                      from ui_events where kind='mount' and ts>1e12
                      group by s having n>=4 order by total desc limit 8"""):
    print("   %d mounts -> %sms total   [%s]" % (x["n"], x["total"], x["labels"]))

print("\n== overlapping long tasks? (300ms resolution buckets) ==")
rows = c.execute("""select ts, duration_ms from ui_events where kind='long_task' and ts>1e12
                    order by ts""").fetchall()
worst = None
for i, r in enumerate(rows):
    end = r["ts"] + r["duration_ms"]
    overlap = sum(1 for o in rows if o["ts"] < end and o["ts"] + o["duration_ms"] > r["ts"])
    if worst is None or overlap > worst[1]:
        worst = (r["ts"], overlap)
print("   max concurrent long-task entries at one instant: %d" % worst[1])
print("   (>1 means the Long Task API is reporting nested/overlapping entries)")
