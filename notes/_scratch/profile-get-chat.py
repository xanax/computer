import asyncio, time, httpx, os
from cptr.utils.config import create_token
from cptr.routers import chat as chat_router

timings = {}
_orig_usage = chat_router._get_chat_context_usage
async def timed_usage(*a, **k):
    t = time.perf_counter()
    r = await _orig_usage(*a, **k)
    timings["context_usage"] = (time.perf_counter() - t) * 1000
    return r
chat_router._get_chat_context_usage = timed_usage

_orig_all = chat_router.ChatMessage.get_all_by_chat
calls = {"n": 0, "ms": 0.0}
async def timed_all(chat_id):
    t = time.perf_counter()
    r = await _orig_all(chat_id)
    calls["n"] += 1
    calls["ms"] += (time.perf_counter() - t) * 1000
    return r
chat_router.ChatMessage.get_all_by_chat = staticmethod(timed_all)

from cptr.app import app
CHAT = os.environ.get("CHAT_ID", "4c61866d-0c4e-4bfc-908a-75abe1ee0747")
TOK = create_token("cf65274f-842c-4d4d-a77f-25facfa186e2", "brendan", "admin")

async def main():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", cookies={"cptr_session": TOK}, timeout=120) as c:
        await c.get(f"/api/chats/{CHAT}")  # warm
        calls["n"] = 0; calls["ms"] = 0.0
        t = time.perf_counter()
        r = await c.get(f"/api/chats/{CHAT}")
        total = (time.perf_counter() - t) * 1000
    print(f"chat {CHAT}")
    print(f"  response        {len(r.content)/1e6:6.2f} MB in {total:7.0f} ms (no gzip)")
    print(f"  context_usage   {timings.get('context_usage', 0):7.0f} ms")
    print(f"  get_all_by_chat {calls['ms']:7.0f} ms over {calls['n']} call(s)")
asyncio.run(main())
