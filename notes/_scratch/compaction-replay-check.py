"""Replay real chats through _load_message_history and report what it keeps.

Verification harness for the "newest checkpoint wins" fix (B-011). Run it
against a COPY of the live data dir, never the live one:

    python - <<'EOF'
    import sqlite3, pathlib
    dst = pathlib.Path("/tmp/cptr-dbcheck"); dst.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect("/home/brendan/.cptr/app.db")
    out = sqlite3.connect(dst / "app.db")
    src.backup(out)          # folds in the WAL, so the copy is consistent
    out.close(); src.close()
    EOF
    CPTR_DATA_DIR=/tmp/cptr-dbcheck .venv/bin/python notes/_scratch/compaction-replay-check.py

Prints one line per chat whose active branch carries more than one checkpoint:
  <chat_id>  chain=<n>  checkpoints=<k>@<idx,...>  replayed=<n>  summary=<chars>
and a TOTAL footer. Run it once before and once after a change and compare the
footer — `replayed` dropping with `summary` staying non-zero is the fix working.
"""

import asyncio
import os
import pathlib
import sqlite3
import sys

DATA = pathlib.Path(os.environ["CPTR_DATA_DIR"])
con = sqlite3.connect(f"file:{DATA / 'app.db'}?mode=ro", uri=True)

parent: dict[str, str | None] = {}
summaries: dict[str, str | None] = {}
for mid, pid, cs in con.execute("select id, parent_id, chat_summary from chat_messages"):
    parent[mid] = pid
    summaries[mid] = cs

chats = list(
    con.execute("select id, current_message_id from chats where current_message_id is not null")
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from cptr.utils.chat_task import _load_message_history  # noqa: E402


def chain_of(leaf: str) -> list[str]:
    out, cur = [], leaf
    while cur:
        out.append(cur)
        cur = parent.get(cur)
    out.reverse()
    return out


async def main() -> None:
    rows = []
    for chat_id, leaf in chats:
        chain = chain_of(leaf)
        cps = [i for i, m in enumerate(chain) if summaries.get(m)]
        messages, summary = await _load_message_history(chat_id, leaf)
        rows.append((chat_id, len(chain), cps, len(messages), len(summary or "")))

    stacked = [r for r in rows if len(r[2]) > 1]
    for chat_id, chain_len, cps, replayed, s_len in stacked:
        idx = ",".join(map(str, cps))
        print(
            f"{chat_id[:8]}  chain={chain_len:3d}  checkpoints={len(cps)}@{idx}"
            f"  replayed={replayed:3d}  summary={s_len}"
        )
    print(
        f"\nTOTAL chats={len(rows)} with_a_checkpoint={sum(1 for r in rows if r[2])}"
        f" stacked={len(stacked)}"
    )
    print(f"TOTAL messages replayed={sum(r[3] for r in rows)}")


asyncio.run(main())
