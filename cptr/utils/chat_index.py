"""Build the search-index entry for a chat — the text stored in `chats.summary`.

`chats.summary` is ranked by `Chat.search_by_text` (`cptr/models/chats.py:506`,
rank 40, *above* raw message content) and returned by the chat list, the
single-chat read, `/api/search` and the `search_chats` tool. This module is the
one place that decides what goes in it, so `scripts/backfill-chat-index.py` and
`POST /api/chats/{chat_id}/index` cannot drift apart.

Nothing here calls a model. The entry is composed from what the chat already
stores, which is the whole point: 56 MB of the 58 MB `chat_messages` table is
tool activity that search and read otherwise cannot see at all, and the file
paths in `function_call.arguments` are a searchable handle on it. See
`.agent-kb/areas/chat-index-summary.md`.

**Two omissions in the composed form are deliberate and load-bearing:** no
English labels ("Files:", "Touched:") and no dates. The composed text is
substring-matched by the ranker, so a word present in every row — `files`, or a
year — would make every chat match that query and flood search results. Keep
that property if the format is ever changed.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

# `summary` is a search snippet and a rank input, not a document.
MAX_ENTRY = 1600
MAX_CHECKPOINT = 1100
MAX_LATER = 400
MAX_QUESTION = 300
MAX_PATHS = 18
MAX_TOOLS = 8


@dataclass(slots=True)
class IndexMessage:
    """The four fields of a message this module needs, from either data layer.

    `output` is the stream items (some of type `function_call`). It arrives as
    **either** shapes: raw JSON *text* from a direct `sqlite3` read (the
    backfill script), or an already-decoded *list* from the ORM, where
    `ChatMessage.output` is a `Column(JSON)`. Handle both — assuming text makes
    the endpoint silently produce entries with no tool activity at all.

    `chat_summary` is the compaction checkpoint, non-empty on the message where
    older history was folded away.
    """

    role: str
    content: str
    output: object
    chat_summary: str | None
    created_at: int


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
    workspace = (workspace or "").rstrip("/")
    if workspace and path.startswith(workspace + "/"):
        return path[len(workspace) + 1 :]
    return path


def paths_in(arguments: object) -> list[str]:
    """Pull path-like values out of a tool call's arguments.

    `arguments` is usually a dict (`{'path': 'README.md'}`) but arrives as a JSON
    string often enough to normalise first.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            return []
    if not isinstance(arguments, dict):
        return []

    found: list[str] = []
    for key, value in arguments.items():
        if "path" not in key.lower() and "file" not in key.lower():
            continue
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(v for v in value if isinstance(v, str))
        elif isinstance(value, dict):
            found.extend(v for v in value.values() if isinstance(v, str))
    return found


def iter_tool_calls(messages: Sequence[IndexMessage]) -> Iterator[tuple[int, str, object]]:
    """Yield (created_at, tool_name, arguments) for every `function_call` item."""
    for message in messages:
        raw = message.output
        if not raw:
            continue
        if isinstance(raw, str):
            try:
                items = json.loads(raw)
            except Exception:
                continue
        else:
            items = raw  # already decoded by the ORM's Column(JSON)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("type") == "function_call":
                yield message.created_at, (item.get("name") or ""), item.get("arguments")


def build_entry(messages: Sequence[IndexMessage], workspace: str = "") -> tuple[str, str]:
    """Return (entry, kind), where kind is `checkpoint`, `composed` or `empty`.

    Precedence:

    1. A compaction checkpoint exists — reuse its prose. A checkpoint only
       describes the messages folded away *before* it, so the paths touched
       after it are appended as a second line.
    2. Otherwise compose the opening question plus the file paths and tool names
       the chat's tool calls touched.
    """
    checkpoint: IndexMessage | None = None
    for message in messages:
        if (message.chat_summary or "").strip():
            checkpoint = message

    paths: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    for created_at, name, arguments in iter_tool_calls(messages):
        if name:
            tools[name] += 1
        if checkpoint is not None and created_at <= checkpoint.created_at:
            continue  # paths before the checkpoint are already in its prose
        for path in paths_in(arguments):
            cleaned = relpath(collapse(path), workspace)
            # Dropping away-from-workspace absolutes keeps /tmp and other
            # workspaces' paths out of the index; costs ~4% of path values.
            if cleaned and not cleaned.startswith("/"):
                paths[cleaned] += 1

    if checkpoint is not None:
        entry = truncate(collapse(checkpoint.chat_summary), MAX_CHECKPOINT)
        if paths:
            later = ", ".join(path for path, _ in paths.most_common(MAX_PATHS))
            entry = f"{entry}\n{truncate(later, MAX_LATER)}"
        return entry[:MAX_ENTRY], "checkpoint"

    lines: list[str] = []
    for message in messages:
        if message.role == "user" and (message.content or "").strip():
            lines.append(truncate(collapse(message.content), MAX_QUESTION))
            break
    if paths:
        lines.append(", ".join(path for path, _ in paths.most_common(MAX_PATHS)))
    if tools:
        lines.append(", ".join(f"{name}×{count}" for name, count in tools.most_common(MAX_TOOLS)))

    entry = truncate("\n".join(lines).strip(), MAX_ENTRY)
    return entry, "composed" if entry else "empty"
