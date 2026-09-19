"""A/B the real _load_message_history: pre-fix code vs working tree, live data.

Sibling of compaction-replay-check.py. That one reimplements the slicing rule
and counts how many messages each turn would replay; this one calls the actual
function, twice, with the pre-fix module loaded alongside the current one, so
the comparison cannot drift from the code being shipped.

    CPTR_DATA_DIR=/tmp/cptr-dbcheck .venv/bin/python \
        notes/_scratch/compaction-realfn-check.py

Reads only. Never point CPTR_DATA_DIR at the live ~/.cptr while the server is
running; copy the DB first (sqlite3 backup, or `.backup` via the Python API).
"""

import asyncio
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("COMPACTION_BASE", "d66bd9d")  # commit before the fix
sys.path.insert(0, REPO)  # running from notes/_scratch puts that dir on sys.path, not the repo
DB = os.path.join(os.environ.get("CPTR_DATA_DIR", ""), "app.db")


def load(path, name):
    """Import a module from an arbitrary path, keeping relative imports intact."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def targets(con):
    """(chat_id, deepest message id, checkpoint count) for stacked-checkpoint chats."""
    rows = con.execute(
        "SELECT chat_id, id FROM chat_messages "
        "WHERE chat_summary IS NOT NULL AND chat_summary != '' ORDER BY chat_id, id"
    ).fetchall()
    by_chat = {}
    for cid, mid in rows:
        by_chat.setdefault(cid, []).append(mid)

    out = []
    for cid, mids in sorted(by_chat.items()):
        if len(mids) < 2:  # one checkpoint: a scan and a break agree
            continue
        tip = mids[-1]
        while True:
            nxt = con.execute(
                "SELECT id FROM chat_messages WHERE parent_id = ? ORDER BY id DESC LIMIT 1",
                (tip,),
            ).fetchone()
            if not nxt:
                break
            tip = nxt[0]
        out.append((cid, tip, len(mids)))
    return out


async def main():
    if not os.path.exists(DB):
        sys.exit(f"no DB at {DB} (set CPTR_DATA_DIR to a copy)")
    old_src = subprocess.run(
        ["git", "show", f"{BASE}:cptr/utils/chat_task.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(old_src)
        old_path = fh.name

    new = load(os.path.join(REPO, "cptr/utils/chat_task.py"), "cptr.utils.chat_task")
    old = load(old_path, "cptr.utils.chat_task_prefix")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    chats = targets(con)
    print(f"chats carrying stacked checkpoints: {len(chats)}\n")

    msgs_old = msgs_new = diff = 0
    for cid, tip, n in chats:
        h_new, s_new = await new._load_message_history(cid, tip)
        h_old, s_old = await old._load_message_history(cid, tip)
        msgs_old += len(h_old)
        msgs_new += len(h_new)
        diff += s_new != s_old
        if diff <= 5 and len(chats) > 5:
            print(f"chat {cid[:8]}  checkpoints={n}")
            print(f"  new: {len(h_new):3d} msgs  summary={(s_new or '')[:52]!r}")
            print(f"  old: {len(h_old):3d} msgs  summary={(s_old or '')[:52]!r}")

    pct = 100 * (msgs_old - msgs_new) / msgs_old if msgs_old else 0
    print(f"\nacross {len(chats)} chats, resumed history {msgs_old} -> {msgs_new} messages "
          f"({pct:.0f}% less)")
    print(f"summary differs in {diff}/{len(chats)}; chats where the newest checkpoint")
    print("happens to be the first are absent from this set by definition, so they")
    print("are unaffected no matter which rule is used")


asyncio.run(main())
