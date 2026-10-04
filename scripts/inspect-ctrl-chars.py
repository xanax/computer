"""Locate every control character in the two rows the repair would touch."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

DB = Path.home() / ".cptr" / "app.db"
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TARGETS = [
    "5a6588f8-75a0-463a-b1a8-42f972770ea2",
    "34308626-957d-4d70-bb06-e8cc3abc5f53",
]


def walk(value, path, out):
    if isinstance(value, str):
        for m in CTRL.finditer(value):
            out.append((path, m.start(), ord(m.group()), value))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            walk(item, f"{path}[{i}]", out)
    elif isinstance(value, dict):
        for k, item in value.items():
            walk(item, f"{path}.{k}", out)


conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
for mid in TARGETS:
    out = conn.execute(
        "select output from chat_messages where id=?", (mid,)
    ).fetchone()[0]
    items = json.loads(out)
    found: list = []
    walk(items, "", found)
    print(f"=== {mid[:8]}  hits={len(found)}")
    for path, pos, cp, s in found[:10]:
        lo, hi = max(0, pos - 70), pos + 50
        ctx = s[lo:hi].encode("unicode_escape").decode()
        print(f"   U+{cp:04X} at {path or '<root>'}[{pos}]")
        print(f"     ...{ctx}...")
    print()
conn.close()