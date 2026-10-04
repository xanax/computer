#!/usr/bin/env python3
"""Strip NUL / C0 control characters out of persisted chat transcripts.

A provider route (MiniMax behind OpenRouter) emitted NUL bytes at token
boundaries inside tool-call arguments and assistant text. cptr persisted them
verbatim, so every later turn replayed them into the prompt context and the
tools kept failing with "embedded null byte" / "lstat: embedded null
character" -- which reads to the user as the agent crashing.

The stream now guards new turns (cptr/utils/chat_task.py). This repairs what
is already on disk. Read-only by default; pass --write to commit.

    scripts/repair-nul-control-chars.py --dry-run
    scripts/repair-nul-control-chars.py --write
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DEFAULT_DB = Path.home() / ".cptr" / "app.db"
TARGET_CHAT = "0717880f-8203-4cca-93c0-c13b6a0d0214"


def clean(value):
    if isinstance(value, str):
        return CONTROL_RE.sub("", value)
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    return value


def count_hits(text: str) -> int:
    return len(CONTROL_RE.findall(text or ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--chat", default="", help="limit to one chat_id")
    parser.add_argument(
        "--write", action="store_true", help="apply changes (default is dry-run)"
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"no such db: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    where, params = "", []
    if args.chat:
        where = "where chat_id = ?"
        params = [args.chat]

    rows = conn.execute(
        f"select id, chat_id, content, output from chat_messages {where}", params
    ).fetchall()

    changed = 0
    total_hits = 0
    for row in rows:
        new_content = clean(row["content"])
        new_output = clean(row["output"])
        hits = count_hits(row["content"]) + count_hits(row["output"])
        if not hits:
            continue
        total_hits += hits
        changed += 1
        print(f"  {row['id']}  chat={row['chat_id']}  control_chars={hits}")
        if args.write:
            conn.execute(
                "update chat_messages set content = ?, output = ? where id = ?",
                (new_content, new_output, row["id"]),
            )

    if args.write and changed:
        conn.commit()
        print(f"\nrepaired {changed} message(s), {total_hits} control character(s) removed")
    else:
        print(
            f"\n{'would repair' if not args.write else 'nothing to do'}: "
            f"{changed} message(s), {total_hits} control character(s)"
        )
        if not args.write and changed:
            print("re-run with --write to apply")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
