"""Skeleton with long *argument* strings clipped too (labels only need short fields)."""
import json, sqlite3, collections

db = sqlite3.connect("file:/home/brendan/.cptr/app.db?mode=ro", uri=True)
ARG_CAP = 200

def clip(v):
    if isinstance(v, str) and len(v) > ARG_CAP:
        return v[:ARG_CAP]
    if isinstance(v, dict):
        return {k: clip(x) for k, x in v.items()}
    if isinstance(v, list):
        return [clip(x) for x in v]
    return v

def skel_item(item):
    t = item.get("type")
    if t == "function_call_output":
        return {"type": t, "call_id": item.get("call_id"), "chars": len(str(item.get("output", "")))}
    if t == "reasoning":
        txt = "".join(p.get("text", "") for p in (item.get("summary") or item.get("content") or []))
        return {"type": t, "id": item.get("id"), "status": item.get("status"), "chars": len(txt)}
    if t == "function_call":
        out = dict(item)
        # ask_user renders its questions inline (the row is always expanded), so keep it whole.
        if item.get("name") != "ask_user":
            out["arguments"] = clip(item.get("arguments") or {})
        return out
    return item

rows = db.execute("select chat_id, output from chat_messages").fetchall()
per = collections.defaultdict(lambda: [0, 0])
for cid, output in rows:
    out = (json.loads(output) if output else None) or []
    per[cid][0] += len(json.dumps(out))
    per[cid][1] += len(json.dumps([skel_item(i) for i in out]))

f = sum(p[0] for p in per.values()); s = sum(p[1] for p in per.values())
print(f"{'chat_id':<38} {'full MB':>8} {'skel MB':>8} {'cut':>6}")
for cid, p in sorted(per.items(), key=lambda kv: -kv[1][0])[:8]:
    print(f"{cid:<38} {p[0]/1e6:8.2f} {p[1]/1e6:8.2f} {1-p[1]/p[0]:5.0%}")
print(f"\noutput items only, all {len(per)} chats: {f/1e6:.1f} MB -> {s/1e6:.1f} MB ({1-s/f:.0%} cut)")
