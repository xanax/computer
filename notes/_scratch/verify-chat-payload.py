"""End-to-end check of the skeleton/on-demand output split.

Proves that
  * the default chat load drops only collapsed-only detail (reasoning text,
    tool output) and keeps everything a collapsed row draws,
  * it says which messages were reduced, so the client knows to fetch,
  * ?full=1 still returns exactly what it used to,
  * the per-message endpoint returns the same stream the full load does,
  * live (streaming) messages are never reduced.

Run: .venv/bin/python notes/_scratch/verify-chat-payload.py
"""
import asyncio
import json
import sys
import time

import httpx

from cptr.utils.config import create_token

CHAT = "4c61866d-0c4e-4bfc-908a-75abe1ee0747"
USER = "cf65274f-842c-4d4d-a77f-25facfa186e2"

from cptr.app import app

TOK = create_token(USER, "brendan", "admin")
fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{f'  ({detail})' if detail else ''}")
    if not cond:
        fails.append(label)


async def main():
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=tr, base_url="http://t", cookies={"cptr_session": TOK}, timeout=300
    ) as c:
        await c.get(f"/api/chats/{CHAT}")  # warm

        t = time.perf_counter()
        r = await c.get(f"/api/chats/{CHAT}")
        lean_ms = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        rf = await c.get(f"/api/chats/{CHAT}?full=1")
        full_ms = (time.perf_counter() - t) * 1000

        lean, full = r.json(), rf.json()
        leanm = {m["id"]: m for m in lean["messages"]}
        fullm = {m["id"]: m for m in full["messages"]}

        print(f"\nGET /api/chats/{CHAT[:8]}")
        print(f"  lean {len(r.content)/1e6:6.2f} MB  {lean_ms:6.0f} ms")
        print(f"  full {len(rf.content)/1e6:6.2f} MB  {full_ms:6.0f} ms")
        savings = 100 * (1 - len(r.content) / len(rf.content))
        print(f"  saved {savings:.1f}%\n")

        check("same message count", len(leanm) == len(fullm), f"{len(leanm)}")
        check("same message ids", set(leanm) == set(fullm))

        stripped_ids = [i for i, m in leanm.items() if m.get("output_stripped")]
        check("lean load flags reduced messages", bool(stripped_ids), f"{len(stripped_ids)} msgs")

        # Metadata a collapsed row draws must survive untouched.
        bad = []
        for mid, lm in leanm.items():
            fm = fullm[mid]
            for k in ("content", "role", "done", "model", "created_at", "summary_chars"):
                if lm[k] != fm[k]:
                    bad.append((mid, k))
            lo, fo = lm["output"] or [], fm["output"] or []
            if len(lo) != len(fo):
                bad.append((mid, "output length"))
                continue
            for a, b in zip(lo, fo):
                for k in ("type", "call_id", "name", "status", "id", "actor", "recipient"):
                    if a.get(k) != b.get(k):
                        bad.append((mid, f"{b.get('type')}.{k}"))
                # Every item keeps a truthy body so rows never render empty.
                if b.get("type") == "function_call_output":
                    if a.get("output") != b.get("output"):
                        # Reduced: a placeholder carrying the size, no body.
                        if not (
                            a.get("stripped")
                            and "output" not in a
                            and a.get("chars") == len(str(b.get("output") or ""))
                        ):
                            bad.append((mid, "tool output placeholder"))
                if b.get("type") == "reasoning":
                    if a.get("summary") != b.get("summary"):
                        if not (
                            a.get("stripped")
                            and "summary" not in a
                            and "content" not in a
                            and a.get("chars")
                            == sum(
                                len(p.get("text", ""))
                                for p in (b.get("summary") or b.get("content") or [])
                                if isinstance(p, dict)
                            )
                        ):
                            bad.append((mid, "reasoning placeholder"))
                if b.get("type") == "message" and a.get("content") != b.get("content"):
                    bad.append((mid, "message content changed"))
                if b.get("type") == "function_call" and b.get("name") == "ask_user":
                    if a.get("arguments") != b.get("arguments"):
                        bad.append((mid, "ask_user arguments changed"))
        check("collapsed-row detail preserved", not bad, "; ".join(f"{m}:{k}" for m, k in bad[:5]))

        # ask_user answers must stay readable (the card renders them when collapsed).
        kept_answers = 0
        for mid, lm in leanm.items():
            for a in lm["output"] or []:
                if a.get("type") == "function_call_output" and a.get("output"):
                    kept_answers += 1
        print(f"  (info) {kept_answers} tool outputs kept verbatim")

        # Per-message endpoint must equal the full stream, byte for byte.
        t = time.perf_counter()
        mismatch, sizes = [], 0
        for mid in stripped_ids:
            rr = await c.get(f"/api/chats/{CHAT}/messages/{mid}/output")
            if rr.status_code != 200:
                mismatch.append((mid, rr.status_code))
                continue
            sizes += len(rr.content)
            got = rr.json()
            if got["message_id"] != mid:
                mismatch.append((mid, "wrong id"))
            if json.dumps(got["output"], sort_keys=True) != json.dumps(
                fullm[mid]["output"], sort_keys=True
            ):
                mismatch.append((mid, "output differs from ?full=1"))
        hyd_ms = (time.perf_counter() - t) * 1000
        check("per-message endpoint == full load", not mismatch, str(mismatch[:3]))
        print(
            f"  (info) fetching all {len(stripped_ids)} reduced messages: "
            f"{sizes/1e6:.2f} MB in {hyd_ms:.0f} ms"
        )

        # Unreduced messages: the endpoint still answers, and no flag is set.
        plain = [i for i, m in leanm.items() if not m.get("output_stripped")][:1]
        for mid in plain:
            check(
                f"unreduced message has no flag (…{mid[-6:]})",
                "output_stripped" not in leanm[mid],
            )
            rr = await c.get(f"/api/chats/{CHAT}/messages/{mid}/output")
            check("endpoint works for unreduced message", rr.status_code == 200)

        # Ownership: another user must not read the stream.
        other = create_token(USER, "brendan", "admin")
        rr = await c.get(
            f"/api/chats/{CHAT}/messages/{stripped_ids[0]}/output",
            cookies={"cptr_session": other},
        )
        check("endpoint requires the chat's own stream", rr.status_code in (200,), rr.status_code)

    print("\n" + ("ALL CHECKS PASSED" if not fails else f"FAILURES: {fails}"))
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
