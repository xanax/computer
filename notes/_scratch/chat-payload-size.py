import json, sqlite3, collections
db = sqlite3.connect("file:/home/brendan/.cptr/app.db?mode=ro", uri=True)
rows = db.execute("select id, chat_id, role, content, output, meta, usage, chat_summary from chat_messages").fetchall()
per_chat = collections.defaultdict(lambda: collections.Counter())
tot = collections.Counter()
for mid, cid, role, content, output, meta, usage, summary in rows:
    parts = collections.Counter()
    parts["content"] = len(content or "")
    parts["chat_summary"] = len(summary or "")
    parts["meta"] = len(json.dumps(meta)) if meta else 0
    parts["usage"] = len(json.dumps(usage)) if usage else 0
    out = (json.loads(output) if output else None) or []
    for item in out:
        t = item.get("type", "?")
        n = len(json.dumps(item))
        if t == "reasoning":
            parts["reasoning"] += n
        elif t == "function_call_output":
            parts["tool_output"] += n
        elif t == "function_call":
            parts["tool_call"] += n
        else:
            parts["other_output"] += n
    tot.update(parts)
    c = per_chat[cid]
    c["msgs"] += 1
    c.update(parts)
    c["bytes"] += sum(parts.values())

print("== totals over", len(rows), "messages ==")
for k, v in tot.most_common():
    print(f"  {k:<16} {v/1e6:8.2f} MB")
print(f"  {'TOTAL':<16} {sum(tot.values())/1e6:8.2f} MB")
print()
print("== top 12 chats by response size (as GET /api/chats/{id} serializes today) ==")
print(f"{'chat_id':<38} {'msgs':>4} {'total MB':>9} {'reason':>8} {'tool_out':>9} {'answer':>8}")
for cid, c in sorted(per_chat.items(), key=lambda kv: -kv[1]["bytes"])[:12]:
    print(f"{cid:<38} {c['msgs']:>4} {c['bytes']/1e6:9.2f} {c['reasoning']/1e6:8.2f} {c['tool_output']/1e6:9.2f} {(c['content']+c['other_output'])/1e6:8.2f}")
print()
live = db.execute("select count(*) from chats where closed_at is null").fetchone()[0]
print("open chats:", live, "of", db.execute("select count(*) from chats").fetchone()[0])
