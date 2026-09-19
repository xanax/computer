"""How many times a single chat load re-reads the message table, and how long it spends.

One GET /api/chats/{id} used to call ``get_all_by_chat`` four times — the same
rows, with the ``output`` column (all the bulk) selected and JSON-decoded each
time — to answer three questions that need only id/parent/role/usage. This counts
the calls and times each kind against the real loader, so the fix is checked by
the SQL actually issued rather than by reading the code.

Run against either revision:
    PYTHONPATH=. .venv/bin/python notes/_scratch/chat-load-query-count.py
"""
import asyncio
import time

import httpx

from cptr.utils.config import create_token

CHAT = "4c61866d-0c4e-4bfc-908a-75abe1ee0747"
USER = "cf65274f-842c-4d4d-a77f-25facfa186e2"

from cptr.app import app
from cptr.models.chats import ChatMessage

stats: dict[str, list] = {}


def wrap(name, key):
    orig = getattr(ChatMessage, name)
    stats[key] = []

    async def counted(*a, **k):
        t = time.perf_counter()
        result = await orig(*a, **k)
        stats[key].append((time.perf_counter() - t) * 1000)
        return result

    setattr(ChatMessage, name, staticmethod(counted))


# Selected columns decide whether the bulk (output) is read; each of these is a
# different query, so counting them shows what a load really touches.
for _name in ("get_all_by_chat", "get_headers_by_chat", "get_last_message_id", "get_last_model"):
    if hasattr(ChatMessage, _name):
        wrap(_name, _name)


async def main():
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=tr, base_url="http://t",
        cookies={"cptr_session": create_token(USER, "brendan", "admin")}, timeout=300,
    ) as c:
        await c.get(f"/api/chats/{CHAT}")  # warm
        for values in stats.values():
            values.clear()
        t = time.perf_counter()
        r = await c.get(f"/api/chats/{CHAT}")
        total = (time.perf_counter() - t) * 1000

    print(f"\nGET /api/chats/{CHAT[:8]}  ->  {len(r.content)/1e6:.2f} MB, {total:.0f} ms")
    for key, times in stats.items():
        if not times:
            continue
        detail = ", ".join(f"{t:.0f}" for t in times)
        print(f"  {key:<22} x{len(times)}  {sum(times):7.0f} ms  ({detail})")


asyncio.run(main())
