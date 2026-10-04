"""Tally control characters in persisted output BY codepoint and BY json field.

Read-only. Distinguishes real provider corruption (NUL, ESC) from
legitimate whitespace, so the repair script is never run blind.
"""

from __future__ import annotations

import collections
import json
import re
import sqlite3
from pathlib import Path

DB = Path.home() / ".cptr" / "app.db"
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

per_cp: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
rows_with: collections.Counter = collections.Counter()
examples: dict[tuple[int, str], str] = {}


def walk(value, path: str) -> None:
    if isinstance(value, str):
        for ch in CONTROL_RE.findall(value):
            cp = ord(ch)
            per_cp[cp][path] += 1
            rows_with[cp] += 1
            examples.setdefault((cp, path), path)
    elif isinstance(value, list):
        for item in value:
            walk(item, path + "[]")
    elif isinstance(value, dict):
        for key, item in value.items():
            walk(item, path + "." + key)


conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.text_factory = str
scan = fail = 0
for mid, output in conn.execute(
    "select id, coalesce(output,'') from chat_messages where output not in ('','null')"
):
    try:
        items = json.loads(output)
    except ValueError:
        fail += 1
        continue
    scan += 1
    walk(items, mid)

print(f"rows parsed: {scan}   unparseable: {fail}\n")
print(f"{'cp':>8}  {'count':>7}  top fields")
for cp, fields in sorted(per_cp.items(), key=lambda kv: -sum(kv[1].values())):
    total = sum(fields.values())
    top = ", ".join(f"{k or '<root>'}={v}" for k, v in fields.most_common(3))
    label = {0: "NUL", 1: "SOH", 4: "EOT", 7: "BEL", 16: "DLE", 27: "ESC", 127: "DEL"}.get(cp, "?")
    print(f"U+{cp:04X} {label:>4} {total:7}  {top}")
conn.close()