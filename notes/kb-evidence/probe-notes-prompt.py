"""Render the real system prompt for a live workspace from a copied data dir.

Run with CPTR_DATA_DIR pointing at the instance to read. It adds a note (through
the model layer, as the API does), renders the prompt the way a chat run does,
and reports whether the block is there.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path("/home/brendan/computer")
sys.path.insert(0, str(REPO))

WS = os.environ.get("WS", "/home/brendan/computer")
USER = os.environ.get("USER_ID", "cf65274f-842c-4d4d-a77f-25facfa186e2")

from starlette.requests import Request  # noqa: E402


def make_request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/__probe__",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("test", 0),
            "scheme": "http",
        }
    )
    request.state.auth = None
    return request


async def main() -> None:
    from cptr.models import Workspace
    from cptr.utils.prompt_templates import load_system_prompt

    await Workspace.add_note(USER, WS, "integration probe: the block should be here.", "agent")
    notes = await Workspace.get_notes(USER, WS)
    print("notes in db:", notes)
    prompt = await load_system_prompt(make_request(), WS, "deepseek-flash", user_id=USER)
    idx = prompt.find("[WORKSPACE NOTES]")
    print("has block:", idx != -1)
    if idx != -1:
        print("--- around the block ---")
        print(prompt[max(0, idx - 400) : idx + 500])
    print("has workspace prompt block:", "[WORKSPACE PROMPT]" in prompt)
    print("prompt chars:", len(prompt))
    await Workspace.delete_note(USER, WS, notes[-1]["id"]) if notes else None


asyncio.run(main())
