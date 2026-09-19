import asyncio, time, httpx, json
from cptr.utils.config import create_token
from cptr.routers import chat as chat_router
from cptr.utils import context as ctx_utils
from cptr.utils import chat_task as chat_task_utils

CHAT = "4c61866d-0c4e-4bfc-908a-75abe1ee0747"
T = {}

def wrap(mod, name, key):
    orig = getattr(mod, name)
    if asyncio.iscoroutinefunction(orig):
        async def f(*a, **k):
            t = time.perf_counter(); r = await orig(*a, **k)
            T[key] = T.get(key, 0) + (time.perf_counter() - t) * 1000; return r
    else:
        def f(*a, **k):
            t = time.perf_counter(); r = orig(*a, **k)
            T[key] = T.get(key, 0) + (time.perf_counter() - t) * 1000; return r
    setattr(mod, name, f)

wrap(chat_router.ChatMessage, "get_all_by_chat", "db: get_all_by_chat")
wrap(chat_router, "_get_chat_context_usage", "> context_usage (total)")
wrap(chat_task_utils, "_load_message_history", "  db: _load_message_history")
wrap(chat_task_utils, "_load_system_prompt", "  sysprompt+workspace")
wrap(ctx_utils, "estimate_messages_tokens", "  estimate_messages_tokens")
wrap(ctx_utils, "estimate_context_usage", "  estimate_context_usage")

from cptr.app import app
TOK = create_token("cf65274f-842c-4d4d-a77f-25facfa186e2", "brendan", "admin")

async def main():
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://t",
                                 cookies={"cptr_session": TOK}, timeout=120) as c:
        await c.get(f"/api/chats/{CHAT}")
        T.clear()
        t = time.perf_counter()
        r = await c.get(f"/api/chats/{CHAT}")
        total = (time.perf_counter() - t) * 1000
    print(f"GET /api/chats/{CHAT[:8]}  ->  {len(r.content)/1e6:.2f} MB, {total:.0f} ms\n")
    acc = 0.0
    for k, v in T.items():
        mark = "" if k.startswith(">") else "   "
        if not k.startswith(">"):
            acc += v
        print(f"  {k:<34} {v:7.0f} ms{mark}")
    print(f"\n  {'sum of instrumented':<34} {sum(T.values()):7.0f} ms (nested, so overlaps)")

asyncio.run(main())
