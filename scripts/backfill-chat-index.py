#!/usr/bin/env python3
"""Backfill `chats.summary` — the chat index that search ranks on.

The entry-building lives in `cptr/utils/chat_index.py`, shared with the
`POST /api/chats/{id}/index` endpoint so the two cannot drift. This script is
the bulk runner: the same entry for every chat, out of data already on disk.
**No LLM calls.**

Why it matters: `chats.summary` is ranked by `Chat.search_by_text`
(`cptr/models/chats.py:506`, rank 40 — above raw message content) and returned
by the chat list, `/api/search` and `search_chats`, but it was populated for 0
of 378 chats. Meanwhile 56 MB of the 58 MB `chat_messages` table is tool
activity that search cannot see at all; the file paths in
`function_call.arguments` put a searchable handle on it.

`updated_at` is left untouched on purpose — the sidebar and `/api/search/recent`
sort on it, so bumping it would reshuffle every chat's position in the UI.

Usage
-----
    .venv/bin/python scripts/backfill-chat-index.py                  # dry run
    .venv/bin/python scripts/backfill-chat-index.py --write
    .venv/bin/python scripts/backfill-chat-index.py --force          # rebuild all
    .venv/bin/python scripts/backfill-chat-index.py --chat-id <id> --write
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cptr.utils.chat_index import IndexMessage, build_entry  # noqa: E402


def resolve_db() -> Path:
    """Same resolution as `cptr/env.py:58` without importing the package."""
    data_dir = Path(os.environ.get("CPTR_DATA_DIR", str(Path.home() / ".cptr")))
    return data_dir / "app.db"


def load_messages(conn: sqlite3.Connection, chat_id: str) -> list[IndexMessage]:
    rows = conn.execute(
        "select role, content, output, chat_summary, created_at "
        "from chat_messages where chat_id = ? order by created_at",
        (chat_id,),
    ).fetchall()
    return [
        IndexMessage(
            role=row["role"] or "",
            content=row["content"] or "",
            output=row["output"],
            chat_summary=row["chat_summary"],
            created_at=row["created_at"] or 0,
        )
        for row in rows
    ]


def workspace_of(chat: sqlite3.Row) -> str:
    try:
        return json.loads(chat["meta"] or "{}").get("workspace") or ""
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--write", action="store_true", help="apply (default is a dry run)")
    ap.add_argument("--force", action="store_true", help="overwrite chats that already have a summary")
    ap.add_argument("--limit", type=int, default=0, help="only process the N largest chats")
    ap.add_argument("--chat-id", default="", help="process a single chat")
    ap.add_argument("--show", type=int, default=8, help="dry run: how many entries to print")
    args = ap.parse_args()

    db_path = resolve_db()
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    if args.chat_id:
        chats = conn.execute("select * from chats where id = ?", (args.chat_id,)).fetchall()
    else:
        chats = conn.execute(
            "select c.*, coalesce(sum(length(m.content) + length(coalesce(m.output,''))), 0) as bytes "
            "from chats c left join chat_messages m on m.chat_id = c.id "
            "group by c.id order by bytes desc"
        ).fetchall()

    if args.limit:
        chats = chats[: args.limit]

    plan: list[tuple[str, str, str]] = []  # (chat_id, entry, kind)
    skipped = 0
    kinds = Counter()
    shown = Counter()
    for chat in chats:
        if not args.force and (chat["summary"] or "").strip():
            skipped += 1
            continue
        entry, kind = build_entry(load_messages(conn, chat["id"]), workspace_of(chat))
        if kind == "empty":
            skipped += 1
            continue
        kinds[kind] += 1
        plan.append((chat["id"], entry, kind))
        if args.show and shown[kind] < 3:
            shown[kind] += 1
            print(f"\n── {kind}: {chat['title']!r} ({chat['id'][:8]}) " + "─" * 20)
            print(entry[:600])

    print(
        f"\n{'would write' if not args.write else 'writing'}: {len(plan)} "
        f"(checkpoint {kinds['checkpoint']}, composed {kinds['composed']}), skipped {skipped}"
    )
    if not plan or not args.write:
        if plan:
            print("dry run — pass --write to apply")
        conn.close()
        return 0

    # ── Writes: one short transaction, updated_at untouched ──
    # The server holds this DB open in WAL mode; a single IMMEDIATE transaction
    # with a busy timeout is enough to coexist with it.
    conn.close()
    writer = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    writer.execute("pragma busy_timeout = 30000")
    written = 0
    try:
        writer.execute("begin immediate")
        for chat_id, entry, _kind in plan:
            cur = writer.execute("update chats set summary = ? where id = ?", (entry, chat_id))
            written += cur.rowcount
        writer.execute("commit")
    except Exception:
        writer.execute("rollback")
        raise
    finally:
        writer.close()
    print(f"updated {written} chat rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
