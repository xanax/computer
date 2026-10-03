#!/usr/bin/env python3
"""Backfill `chats.summary` — the chat index that search ranks on.

Why this exists
---------------
`chats.summary` is already read and ranked everywhere, and populated nowhere:

- `Chat.search_by_text` (`cptr/models/chats.py:503`) ranks a query against
  id / title / **summary** / message content, with `summary` at rank 40 —
  *above* raw message content at 50+.
- It is returned by `search_chats` (`cptr/utils/tools.py:3268`), `/api/search`
  (`cptr/routers/search.py:92`) and the chat list payload (`chat.py:202`).
- `Chat.update_summary` (`chats.py:196`) exists and is used by fork.
- Measured 2026-10-03: **0 of 378 chats** had a value.

Meanwhile 56 MB of the 58 MB `chat_messages` table is tool activity that is
invisible to search entirely (see `notes/NOTES-chat-storage-and-search-audit.md`).

This writes an index entry for every chat out of data already on disk.
**No LLM calls.**

Precedence
----------
1. The chat has a compaction checkpoint (`chat_messages.chat_summary`) — use it
   verbatim, trimmed. A checkpoint only describes the messages that were dropped
   *before* it, so the paths touched *after* it are appended.
2. Otherwise compose from the opening question plus the file paths and tool names
   the chat's `function_call` items touched.

Two deliberate omissions in the composed form: no English filler labels and no
dates. `summary` is substring-matched by the ranker, so a literal "Files:" or a
year present in all 378 rows would make every chat match the query "files" or
"2026" and flood search results.

`updated_at` is left untouched on purpose — the sidebar and `/api/search/recent`
sort on it, so bumping it would reshuffle every chat's position in the UI.

Usage
-----
    .venv/bin/python scripts/backfill-chat-index.py                  # dry run
    .venv/bin/python scripts/backfill-chat-index.py --write
    .venv/bin/python scripts/backfill-chat-index.py --write --force  # overwrite
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

# ── Limits ──────────────────────────────────────────────────
# `summary` is a search snippet and a rank input, not a document.
MAX_ENTRY = 1600
MAX_CHECKPOINT = 1100
MAX_LATER = 400
MAX_QUESTION = 300
MAX_PATHS = 18
MAX_TOOLS = 8


def resolve_db() -> Path:
    """Same resolution as `cptr/env.py:58` without importing the package."""
    data_dir = Path(os.environ.get("CPTR_DATA_DIR", str(Path.home() / ".cptr")))
    return data_dir / "app.db"


def collapse(text: str) -> str:
    """One line, no runs of whitespace — keeps the entry greppable."""
    return " ".join((text or "").split())


def truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Prefer a boundary; fall back to a hard cut for unbroken text (paths, code).
    for sep in ("\n", ". ", " "):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return cut[:idx].rstrip() + " …"
    return cut.rstrip() + " …"


def relpath(path: str, workspace: str) -> str:
    """Drop the workspace prefix so paths read as repo-relative."""
    if workspace and path.startswith(workspace.rstrip("/") + "/"):
        return path[len(workspace.rstrip("/")) + 1 :]
    return path


def paths_in(arguments: object) -> list[str]:
    """Pull path-like values out of a tool call's arguments."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            return []
    if not isinstance(arguments, dict):
        return []

    found: list[str] = []
    for key, value in arguments.items():
        key_l = key.lower()
        pathish = "path" in key_l or "file" in key_l
        if not pathish:
            continue
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(v for v in value if isinstance(v, str))
        elif isinstance(value, dict):
            found.extend(v for v in value.values() if isinstance(v, str))
    return found


def iter_tool_calls(rows: list[sqlite3.Row]):
    """Yield (created_at, tool_name, arguments) for every function_call item."""
    for row in rows:
        raw = row["output"]
        if not raw:
            continue
        try:
            items = json.loads(raw)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("type") == "function_call":
                yield row["created_at"], (item.get("name") or ""), item.get("arguments")


def build_entry(rows: list[sqlite3.Row], workspace: str) -> tuple[str, str]:
    """Return (entry, kind) where kind is 'checkpoint' or 'composed'."""
    # ── 1. Newest compaction checkpoint ─────────────────────
    checkpoint: sqlite3.Row | None = None
    for row in rows:
        if (row["chat_summary"] or "").strip():
            checkpoint = row

    paths: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    for created_at, name, arguments in iter_tool_calls(rows):
        if name:
            tools[name] += 1
        if checkpoint is not None and created_at <= checkpoint["created_at"]:
            continue  # paths before the checkpoint are already in its prose
        for path in paths_in(arguments):
            cleaned = relpath(collapse(path), workspace)
            if cleaned and not cleaned.startswith("/"):
                paths[cleaned] += 1

    if checkpoint is not None:
        entry = truncate(collapse(checkpoint["chat_summary"]), MAX_CHECKPOINT)
        if paths:
            later = ", ".join(p for p, _ in paths.most_common(MAX_PATHS))
            entry = f"{entry}\n{truncate(later, MAX_LATER)}"
        return entry[:MAX_ENTRY], "checkpoint"

    # ── 2. Compose from what the chat already contains ──────
    lines: list[str] = []
    for row in rows:
        if row["role"] == "user" and (row["content"] or "").strip():
            lines.append(truncate(collapse(row["content"]), MAX_QUESTION))
            break
    if paths:
        lines.append(", ".join(p for p, _ in paths.most_common(MAX_PATHS)))
    if tools:
        lines.append(", ".join(f"{n}×{c}" for n, c in tools.most_common(MAX_TOOLS)))
    return truncate("\n".join(lines).strip(), MAX_ENTRY), "composed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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

    # Read-only connection for everything; writes go through `writer` below.
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
    for chat in chats:
        if not args.force and (chat["summary"] or "").strip():
            skipped += 1
            continue
        rows = conn.execute(
            "select role, content, output, chat_summary, created_at "
            "from chat_messages where chat_id = ? order by created_at",
            (chat["id"],),
        ).fetchall()
        try:
            workspace = json.loads(chat["meta"] or "{}").get("workspace") or ""
        except Exception:
            workspace = ""
        entry, kind = build_entry(rows, workspace)
        if not entry.strip():
            skipped += 1
            continue
        kinds[kind] += 1
        plan.append((chat["id"], entry, kind))
        if args.show and kinds[kind] <= 3:
            print(f"\n── {kind}: {chat['title']!r} ({chat['id'][:8]}) " + "─" * 20)
            print(entry[:600])

    print(
        f"\n{'would write' if not args.write else 'writing'}: {len(plan)} "
        f"(checkpoint {kinds['checkpoint']}, composed {kinds['composed']}), skipped {skipped}"
    )
    if not plan:
        conn.close()
        return 0

    if not args.write:
        print("dry run — pass --write to apply")
        conn.close()
        return 0

    # ── Writes: one short transaction, updated_at untouched ──
    # The server holds this DB open in WAL mode; a single IMMEDIATE
    # transaction with a busy timeout is enough to coexist with it.
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
