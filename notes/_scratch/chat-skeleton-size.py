"""How much of GET /api/chats/{id} survives a 'collapsed by default' skeleton?

Skeleton rules (only what the frontend renders while groups are collapsed):
  function_call        keep name/arguments/status/call_id  (needed for labels)
  function_call_output drop the payload, keep call_id + size  (loaded on expand)
  reasoning            drop text/summary, keep status + char count
"""
import json, sqlite3, collections, sys

db = sqlite3.connect("file:/home/brendan/.cptr/app.db?mode=ro", uri=True)
rows = db.execute("select chat_id, content, output, meta, usage, chat_summary from chat_messages").fetchall()

def sizes(output):
    full = skel = 0
    for item in output or []:
        n = len(json.dumps(item))
        full += n
        t = item.get("type")
        if t == "function_call_output":
            skel += len(json.dumps({"type": t, "call_id": item.get("call_id"),
                                    "chars": len(str(item.get("output", "")))}))
        elif t == "reasoning":
            txt = "".join(p.get("text", "") for p in (item.get("summary") or item.get("content") or []))
            skel += len(json.dumps({"type": t, "id": item.get("id"),
                                    "status": item.get("status"), "chars": len(txt)}))
        else:
            skel += n
    return full, skel

per = collections.defaultdict(lambda: [0, 0, 0, 0])  # msgs, full, skel, answer
for cid, content, output, meta, usage, summary in rows:
    out = (json.loads(output) if output else None) or []
    f, s = sizes(out)
    rest = len(content or "") + len(json.dumps(meta or {})) + len(json.dumps(usage or {}))
    p = per[cid]
    p[0] += 1; p[1] += f + rest; p[2] += s + rest; p[3] += len(content or "")

print(f"{'chat_id':<38} {'msgs':>4} {'today MB':>9} {'skel MB':>8} {'cut':>7} {'skel %':>7}")
tf = ts = 0
for cid, p in sorted(per.items(), key=lambda kv: -kv[1][1])[:15]:
    tf += p[1]; ts += p[2]
    print(f"{cid:<38} {p[0]:>4} {p[1]/1e6:9.2f} {p[2]/1e6:8.2f} {1-p[2]/p[1]:6.0%} {100*p[2]/p[1]:6.1f}%")
tot_f = sum(p[1] for p in per.values()); tot_s = sum(p[2] for p in per.values())
print(f"\nall {len(per)} chats: today {tot_f/1e6:.1f} MB -> skeleton {tot_s/1e6:.1f} MB ({1-tot_s/tot_f:.0%} cut)")
print(f"open chats only: "
      f"{sum(p[1] for c,p in per.items() if c in {r[0] for r in db.execute('select id from chats where closed_at is null')})/1e6:.1f} MB -> "
      f"{sum(p[2] for c,p in per.items() if c in {r[0] for r in db.execute('select id from chats where closed_at is null')})/1e6:.1f} MB")
