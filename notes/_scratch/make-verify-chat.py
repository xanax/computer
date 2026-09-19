"""Seed a throwaway chat into the verification data dir (~/.cptr-verify).

Six short questions, each followed by a multi-screen answer, so the header's
scrolled-question line has something to walk up through. Run with the repo venv.
"""

import json
import sqlite3
import time
import uuid

DB = "/home/brendan/.cptr-verify/app.db"
USER_ID = "147787f4-e627-4028-b703-8594381460b8"
CHAT_ID = "verify-question-line-chat"

FILLER = (
    "Sentence {n} keeps the answer long on purpose, so that each pair fills more "
    "than one screen and the question above it really is out of sight."
)


def answer(i: int) -> str:
    paras = [f"**Answer {i}.** Paragraph {p} of the reply to question {i}." for p in range(1, 31)]
    body = "\n\n".join(
        paras + [FILLER.format(n=n) for n in range(1, 16)]  # extra bulk
    )
    return f"{body}\n\nEnd of answer {i}."


def main() -> None:
    now = int(time.time() * 1000)
    base = now - 600_000
    rows = []
    ts = base
    for i in range(1, 7):
        ts += 1000
        rows.append((str(uuid.uuid4()), "user", f"Question {i}: what does answer {i} say?", ts))
        ts += 1000
        rows.append((str(uuid.uuid4()), "assistant", answer(i), ts))

    con = sqlite3.connect(DB)
    with con:
        con.execute(
            "insert or replace into chats (id, user_id, title, summary, current_message_id, meta,"
            " created_at, updated_at, last_read_at) values (?,?,?,?,?,?,?,?,?)",
            (
                CHAT_ID,
                USER_ID,
                "Verify scrolled question line",
                None,
                rows[-1][0],
                json.dumps({"model_id": None}),
                base,
                ts,
                ts,
            ),
        )
        con.execute("delete from chat_messages where chat_id=?", (CHAT_ID,))
        for mid, role, content, created in rows:
            con.execute(
                "insert into chat_messages (id, chat_id, parent_id, role, content, model, done,"
                " output, usage, meta, created_at) values (?,?,?,?,?,?,?,?,?,?,?)",
                (mid, CHAT_ID, None, role, content, "verify-model" if role == "assistant" else None,
                 1, None, None, None, created),
            )
    print("seeded", CHAT_ID, len(rows), "messages, updated_at", ts)


if __name__ == "__main__":
    main()
