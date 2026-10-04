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
import json
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


def repair_output(output: str) -> tuple[str, int, int, bool]:
    """Repair a persisted ``output`` JSON column.

    Two hard-won facts shape this:

    1. A raw NUL is almost never present in the *stored text*: json.dumps
       escapes it, so the column holds the six characters ``\\u0000``.
       Grepping the column for a NUL byte finds nothing and the corruption
       only becomes real after json.loads. So decode first, then clean.

    2. Control characters in a *function_call_output* body are often real
       payload, not corruption: terminal output is full of ANSI colour
       escapes (U+001B), and read_url can pull down a binary blob (a census
       found 80KB of eng.traineddata with 30k+ control bytes). Stripping
       those mangles genuine tool results, so tool output bodies are left
       alone.

    Only assistant text, tool-call arguments and invalid call names are
    repaired here. Returns (new_output, chars_removed, calls_dropped,
    skipped_tool_output).
    """
    try:
        items = json.loads(output)
    except (TypeError, ValueError):
        return output, 0, 0, False
    if not isinstance(items, list):
        return output, 0, 0, False

    touched_tool_output = False
    hits = 0
    dropped = 0
    drop_ids: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "function_call_output":
            body = item.get("output")
            if isinstance(body, str) and CONTROL_RE.search(body):
                touched_tool_output = True
            continue
        if itype == "function_call":
            name = item.get("name")
            if isinstance(name, str):
                hits += len(CONTROL_RE.findall(name))
                item["name"] = CONTROL_RE.sub("", name)
            # Arguments carry the original B-021 corruption ("br\x00endan"),
            # but they are stored as a *dict*, not a string, so a string-only
            # check silently misses them.
            if "arguments" in item:
                args, n = _clean_strings(item["arguments"])
                if n:
                    hits += n
                    item["arguments"] = args
            if not isinstance(item.get("name"), str) or not item["name"].strip():
                call_id = item.get("call_id") or item.get("id")
                if isinstance(call_id, str) and call_id:
                    drop_ids.add(call_id)
                dropped += 1
                continue
        elif itype in ("message", "reasoning"):
            # Assistant-visible text. `message` carries `content`; `reasoning`
            # carries its own list, and both may carry `reasoning_details`
            # entries with their own `text` — all three hold provider NULs.
            for key in ("content", "reasoning_details"):
                if key not in item:
                    continue
                cleaned, n = _clean_strings(item[key])
                if n:
                    hits += n
                    item[key] = cleaned

    if drop_ids:
        kept = [
            item
            for item in items
            if not (
                isinstance(item, dict)
                and item.get("type") == "function_call"
                and (item.get("call_id") or item.get("id")) in drop_ids
            )
        ]
        kept = [
            item
            for item in kept
            if not (
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") in drop_ids
            )
        ]
        items = kept

    if not hits and not dropped:
        return output, 0, 0, touched_tool_output
    return json.dumps(items), hits, dropped, touched_tool_output


def _clean_strings(value) -> tuple:
    """Strip control chars from a message content tree.

    Returns (new_value, chars_removed).
    """
    if isinstance(value, str):
        return CONTROL_RE.sub("", value), len(CONTROL_RE.findall(value))
    if isinstance(value, list):
        total = 0
        out = []
        for item in value:
            new_item, n = _clean_strings(item)
            out.append(new_item)
            total += n
        return out, total
    if isinstance(value, dict):
        total = 0
        out = {}
        for key, item in value.items():
            new_item, n = _clean_strings(item)
            out[key] = new_item
            total += n
        return out, total
    return value, 0


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
    total_dropped = 0
    skipped = 0
    for row in rows:
        new_content = clean(row["content"])
        new_output, hits, dropped, skipped_tool_output = repair_output(
            row["output"] or ""
        )
        hits += count_hits(row["content"])
        if skipped_tool_output and not hits and not dropped:
            # Control chars here are ANSI colour codes / binary tool payloads:
            # real data, not corruption. Never rewrite these.
            skipped += 1
            continue
        if not hits and not dropped:
            continue
        total_hits += hits
        total_dropped += dropped
        changed += 1
        print(
            f"  {row['id']}  chat={row['chat_id']}  "
            f"control_chars={hits}  invalid_calls_dropped={dropped}"
        )
        if args.write:
            conn.execute(
                "update chat_messages set content = ?, output = ? where id = ?",
                (new_content, new_output, row["id"]),
            )

    if skipped:
        print(
            f"\nskipped {skipped} message(s) whose only control characters are "
            f"in tool output bodies (ANSI colour / binary payload) — left intact"
        )
    if args.write and changed:
        conn.commit()
        print(
            f"\nrepaired {changed} message(s), {total_hits} control character(s) "
            f"removed, {total_dropped} invalid tool_call(s) dropped"
        )
    else:
        print(
            f"\n{'would repair' if not args.write else 'nothing to do'}: "
            f"{changed} message(s), {total_hits} control character(s), "
            f"{total_dropped} invalid tool_call(s)"
        )
        if not args.write and changed:
            print("re-run with --write to apply")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
