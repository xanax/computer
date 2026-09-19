"""Check the compaction *marker*: what the transcript is told about a checkpoint.

Third sibling of compaction-replay-check.py / compaction-realfn-check.py. Those
two cover which summary bounds the replay; this one covers the two signals the UI
gets from it:

  * `_message_dict()` must flag every checkpoint with `summary_chars` (the length
    of the stored summary) and must NOT ship the summary text itself — that
    text is ~3 KB per checkpoint and reaches the model through the system prompt
    instead, so it has no business in every loadChat response.
  * the mid-turn `chat:compacted` emit must sit inside `run_chat_task`'s compaction
    branch, after the checkpoint is persisted, or the divider only appears on the
    next reload. Checked with `ast` rather than by reading the file, so a later
    refactor that moves the emit out of scope fails here.

    CPTR_DATA_DIR=/tmp/cptr-verify .venv/bin/python \
        notes/_scratch/compaction-marker-check.py

Reads only: the DB is opened with `mode=ro`.
"""

import ast
import asyncio
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
DB = os.path.join(os.environ.get("CPTR_DATA_DIR", ""), "app.db")
CHAT_TASK = os.path.join(REPO, "cptr/utils/chat_task.py")


def check_emit_site():
    """The live-event emit: right function, right place, right payload."""
    tree = ast.parse(open(CHAT_TASK).read())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):  # nested defs keep their own scope, fine here
            if not isinstance(inner, ast.Call):
                continue
            if not any(
                kw.arg == "type" and isinstance(kw.value, ast.Constant)
                and kw.value.value == "chat:compacted"
                for kw in inner.keywords
            ):
                continue
            found.append(
                (node.name, inner.lineno, sorted(kw.arg for kw in inner.keywords))
            )
    assert found, "no chat:compacted emit in chat_task.py"
    for fn, lineno, kwargs in found:
        assert fn == "run_chat_task", f"{fn} declares _emit_payload but {fn}:{lineno} emits it"
        assert set(kwargs) == {"type", "checkpoint_message_id", "summary_chars"}, kwargs
        # The emit must follow the checkpoint write, not precede it.
        update = "ChatMessage.update(checkpoint_message_id"
        before = "".join(open(CHAT_TASK).readlines()[: lineno - 1])
        assert update in before, "emit does not follow the checkpoint write"
        print(f"ok  emit site: {fn}:{lineno} kwargs={kwargs}")
    return len(found)


async def check_payload():
    from cptr.models.chats import ChatMessage
    from cptr.routers.chat import _message_dict

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    chats = [
        row[0]
        for row in con.execute(
            "SELECT chat_id FROM chat_messages WHERE chat_summary IS NOT NULL "
            "AND chat_summary != '' GROUP BY chat_id ORDER BY count(*) DESC"
        )
    ]
    plain = [
        row[0]
        for row in con.execute(
            "SELECT chat_id FROM chat_messages WHERE chat_summary IS NULL "
            "GROUP BY chat_id LIMIT 3"
        )
    ]
    print(f"chats with checkpoints: {len(chats)}, spot-check chats without: {len(plain)}\n")

    marked = leaked = wrong = 0
    checked = 0
    for chat_id in chats + plain:
        for m in await ChatMessage.get_all_by_chat(chat_id):
            d = _message_dict(m)
            checked += 1
            if "chat_summary" in d:
                leaked += 1
            stored = len(m.chat_summary or "")
            marked += d["summary_chars"] > 0
            wrong += d["summary_chars"] != stored
            assert d.keys() == {
                "id", "parent_id", "role", "content", "model", "done", "output",
                "usage", "meta", "created_at", "summary_chars",
            }, sorted(d)
        if checked and len(chats) and chat_id == chats[0]:
            msgs = await ChatMessage.get_all_by_chat(chat_id)
            newest = [m for m in msgs if m.chat_summary][-1]
            print(f"sample chat {chat_id[:8]}: {len(msgs)} msgs, "
                  f"newest checkpoint {newest.id[:8]} -> "
                  f"{_message_dict(newest)['summary_chars']} chars\n")

    print(f"serialised {checked} messages: {marked} carry summary_chars "
          f"(all matching the stored length: {wrong == 0}), "
          f"summary text leaked into the payload: {leaked}")
    assert marked >= len(chats), "a checkpoint went unflagged"
    assert wrong == 0 and leaked == 0
    return marked


def main():
    check_emit_site()
    print()
    asyncio.run(check_payload())
    print("\nmarker contract holds")


main()
