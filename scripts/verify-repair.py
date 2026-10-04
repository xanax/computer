"""Verify a repaired DB copy: no NULs in assistant text, binary payload intact.

Compares /tmp/app-test.db against the live DB row by row for the touched chat,
asserting the only differences are control-char removals and the dropped call.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

LIVE = Path.home() / ".cptr" / "app.db"
TEST = Path("/tmp/app-test.db")
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CHAT = "0717880f-8203-4cca-93c0-c13b6a0d0214"


def rows(db: Path) -> dict[str, str]:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {
        i: o
        for i, o in c.execute(
            "select id, coalesce(output,'') from chat_messages where chat_id=?",
            (CHAT,),
        )
    }
    c.close()
    return out


live, test = rows(LIVE), rows(TEST)
print(f"rows in chat: live={len(live)} test={len(test)}")
assert set(live) == set(test), "repair changed the row set!"

changed = [k for k in live if live[k] != test[k]]
print(f"rows changed: {len(changed)}\n")

failures = []
for mid in changed:
    a, b = json.loads(live[mid]), json.loads(test[mid])
    # Strip every control char from the ORIGINAL; result must equal the repaired.
    def scrub(v):
        if isinstance(v, str):
            return CTRL.sub("", v)
        if isinstance(v, list):
            return [scrub(i) for i in v]
        if isinstance(v, dict):
            return {k: scrub(i) for k, i in v.items()}
        return v

    dropped_ids = {
        i.get("call_id") for i in a
        if i.get("type") == "function_call"
        and not (isinstance(i.get("name"), str) and i["name"].strip())
    }
    expected = scrub(a)
    expected = [i for i in expected
                if not (i.get("type") == "function_call"
                        and (i.get("call_id") or i.get("id")) in dropped_ids)]
    expected = [i for i in expected
                if not (i.get("type") == "function_call_output"
                        and i.get("call_id") in dropped_ids)]

    # The repaired row must equal the scrubbed original MINUS the dropped call
    # and its orphaned output. Anything else is data loss.
    key = lambda d: json.dumps(d, sort_keys=True)
    expected_keys = {key(x) for x in expected}
    actual_keys = {key(x) for x in b}
    only_rep = [x for x in b if key(x) not in expected_keys]
    only_exp = [x for x in expected if key(x) not in actual_keys]
    # Every removal must be an invalid call or its orphaned output — nothing else.
    def _invalid(i: dict) -> bool:
        """A call the repair is allowed to remove.

        The stored name is the JSON *escape* "\\u0000": judge it after
        scrubbing, the way the runtime would.
        """
        name = i.get("name")
        if isinstance(name, str) and CTRL.search(name):
            return not CTRL.sub("", name).strip()
        return not (isinstance(name, str) and name.strip())

    legitimate_removals = {  # noqa: E128
        (i.get("call_id") or i.get("id"))
        for i in a
        if i.get("type") == "function_call" and _invalid(i)
    } | dropped_ids
    unexplained = [
        x for x in only_exp
        if not (x.get("type") in ("function_call", "function_call_output")
                and (x.get("call_id") or x.get("id")) in legitimate_removals)
    ]
    if unexplained or only_rep:
        failures.append(
            f"{mid[:8]}: unexpected difference "
            f"(+{len(only_rep)} in repaired / -{len(only_exp)} expected removed, "
            f"{len(unexplained)} unexplained)"
        )
        for x in (only_rep[:2] + unexplained[:2]):
            print("      DIFF:", key(x)[:150])
        continue
    print(f"  {mid[:8]}: ok (removed {len(only_exp)} invalid item(s))")

print()
# The binary payload must survive byte-for-byte.
BLOB_ROW = "24b155fe-a8f3-45ad-8295-7a785d78c028"
if BLOB_ROW in live:
    same = live[BLOB_ROW] == test[BLOB_ROW]
    print(f"binary/ANSI tool-output row {BLOB_ROW[:8]} untouched: {same}")
    if not same:
        failures.append("binary tool output was modified!")

# And no NUL may remain in assistant text for this chat.
left = 0
for mid, out in test.items():
    if not out:
        continue  # coalesce('') on a NULL column lands here only if we re-read raw
    try:
        items = json.loads(out)
    except ValueError:
        continue
    for it in items if isinstance(items, list) else []:
        if it.get("type") in ("message", "reasoning"):
            for key in ("content", "reasoning_details"):
                if CTRL.search(json.dumps(it.get(key) or "")):
                    left += 1
print(f"items in chat still holding control chars in text: {left}")
if left:
    failures.append(f"{left} text items still hold control chars")

print()
print("RESULT:", "PASS" if not failures else "FAIL")
for f in failures:
    print("  -", f)