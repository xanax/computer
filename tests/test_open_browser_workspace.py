"""``open_browser`` must send the chat's workspace, and the client must use it.

A chat in workspace A calls ``open_browser`` while the user is looking at
workspace B. The event carried a ``workspace`` field all along, but the client
dropped it on the floor and opened the tab wherever it happened to be -- so the
page appeared in the wrong workspace, far from the chat that asked for it.

These tests pin the server half (the field is present and correct) and assert
the client source actually threads it through, since the client's behaviour is
covered by the frontend build rather than by pytest.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

from cptr.socket import main as socket_main
from cptr.utils import tools

FRONTEND = Path(__file__).resolve().parents[1] / "cptr" / "frontend" / "src"


def test_emit_open_browser_carries_the_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The event must name the workspace, not just the url."""
    sent: list[dict] = []

    async def fake_emit_to_user(user_id: str, payload: dict) -> None:
        sent.append({"user_id": user_id, **payload})

    monkeypatch.setattr(socket_main, "emit_to_user", fake_emit_to_user)

    asyncio.run(
        socket_main.emit_open_browser(
            "brendan",
            "http://localhost:5173/",
            chat_id="chat-1",
            workspace="/home/brendan/aijly.com",
            label="Demo",
        )
    )

    assert len(sent) == 1
    event = sent[0]
    assert event["type"] == "open_browser"
    assert event["workspace"] == "/home/brendan/aijly.com"
    assert event["url"] == "http://localhost:5173/"


def test_home_chat_sends_an_empty_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chat with no workspace sends ``""``, not ``None``.

    The client treats an empty value as "open where the user is" and switches
    otherwise; sending ``None`` would stringify into a truthy-looking value on
    the way through JSON and send the user off a workspace that does not exist.
    """
    sent: list[dict] = []

    async def fake_emit_to_user(user_id: str, payload: dict) -> None:
        sent.append(payload)

    monkeypatch.setattr(socket_main, "emit_to_user", fake_emit_to_user)

    asyncio.run(socket_main.emit_open_browser("brendan", "http://localhost:5173/"))

    assert sent[0]["workspace"] == ""


def test_the_tool_passes_the_context_workspace() -> None:
    """``open_browser`` must forward ``__context__['workspace']``.

    Asserted against the source rather than by calling the tool: the tool needs
    a live socket and a listening local port, and the bug was a dropped
    argument, which is a thing the source states plainly.
    """
    source = inspect.getsource(tools.open_browser)
    assert 'workspace = str(__context__.get("workspace") or "")' in source
    assert re.search(
        r"emit_open_browser\(\s*user_id,\s*qualified,\s*chat_id=chat_id,\s*"
        r"workspace=workspace,\s*label=tab_label\s*\)",
        source,
    ), "open_browser no longer forwards the workspace to the event"


def test_client_threads_the_workspace_into_open_local_page() -> None:
    """The socket handler must pass ``data.workspace`` down.

    This is the exact line the bug lived on: it read ``data.url`` and
    ``data.label`` off the event and silently ignored the workspace sitting
    right there in the same object.
    """
    source = (FRONTEND / "lib" / "stores" / "chat.ts").read_text(encoding="utf-8")
    assert re.search(
        r"void openBrowserFromChat\(data\.url,\s*data\.label,\s*data\.workspace\)",
        source,
    ), "open_browser handler still drops data.workspace"


def test_open_local_page_accepts_a_target_workspace() -> None:
    """``openLocalPage`` must take the target workspace and switch to it."""
    source = (FRONTEND / "lib" / "stores.ts").read_text(encoding="utf-8")
    match = re.search(
        r"export async function openLocalPage\(\s*url: string,\s*label\?: string,\s*"
        r"targetWorkspace\?: string\s*\)",
        source,
    )
    assert match, "openLocalPage does not accept a targetWorkspace"
    assert "switchToWorkspace(targetWorkspace)" in source
