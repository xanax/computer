"""``open_browser`` puts the page in the chat's own workspace, not the user's.

A chat in workspace A calls ``open_browser`` while the user is looking at
workspace B. The first version dropped ``workspace`` and opened the tab
wherever it happened to be; the second threaded it through but used it to
*switch*, which yanked the user out of what they were reading to show a page
they had not asked for. Now the tab is written into workspace A's own persisted
layout on the server, the user stays put, and a notification names the workspace
so they can go themselves.

These tests pin the server half (the field is present, the tab is written to
the right workspace, the user is not moved) and assert the client source
threads it through without switching, since the client's behaviour is covered
by the frontend build rather than by pytest.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path

import pytest

from cptr.socket import main as socket_main
from cptr.utils import tools

FRONTEND = Path(__file__).resolve().parents[1] / "cptr" / "frontend" / "src"


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point every workspace write at a throwaway SQLite file.

    `emit_open_browser` now writes a tab into the target workspace's row, so a
    test that calls it with a real-looking path is a test that edits the user's
    actual workspaces. That is not hypothetical: the first version of this file
    passed `/home/brendan/aijly.com` and dropped a "Demo" browser tab into it.
    Autouse so no test here can repeat that.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from cptr.models.base import Base
    from cptr.utils import db as dbmod

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'open-browser.db'}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    monkeypatch.setattr(dbmod, "_engine", engine)
    monkeypatch.setattr(dbmod, "_async_session", factory)
    monkeypatch.setattr(dbmod, "get_engine", lambda: engine)
    monkeypatch.setattr(dbmod, "get_session_factory", lambda: factory)
    yield engine
    asyncio.run(engine.dispose())


def _seed_workspace(user_id: str, path: str) -> None:
    """A workspace with a realistic tab strip (Dash + Files pinned)."""

    async def seed():
        from cptr.models import Workspace

        await Workspace.upsert(
            user_id,
            path,
            Path(path).name,
            {
                "groups": [
                    {
                        "id": "default",
                        "tabs": [
                            {"id": "dash", "type": "dash", "label": "dash"},
                            {"id": "files", "type": "files", "label": "files"},
                        ],
                        "activeTabId": "files",
                    }
                ],
                "activeGroupId": "default",
            },
        )

    asyncio.run(seed())


def test_emit_open_browser_carries_the_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The event must name the workspace, not just the url.

    Uses a path under tmp_path, not a real one: `emit_open_browser` writes into
    the workspace's row, so naming a real workspace here means editing it.
    """
    workspace = str(tmp_path / "ws")
    sent: list[dict] = []

    async def fake_emit_to_user(user_id: str, payload: dict) -> None:
        sent.append({"user_id": user_id, **payload})

    monkeypatch.setattr(socket_main, "emit_to_user", fake_emit_to_user)

    asyncio.run(
        socket_main.emit_open_browser(
            "brendan",
            "http://localhost:5173/",
            chat_id="chat-1",
            workspace=workspace,
            label="Demo",
        )
    )

    events = [e for e in sent if e["type"] == "open_browser"]
    assert len(events) == 1
    event = events[0]
    assert event["workspace"] == workspace
    assert event["workspace_name"] == "ws"
    assert event["url"] == "http://localhost:5173/"
    assert event["chat_id"] == "chat-1"
    assert event["label"] == "Demo"


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


def test_the_tool_tells_the_agent_the_page_did_not_move_the_user() -> None:
    """The agent used to be told the page was simply "opened".

    It is told the workspace it went to, so a chat that opened a page for a user
    working somewhere else knows the page is not in front of them and can say so
    rather than assume it is.
    """
    source = inspect.getsource(tools.open_browser)
    assert 'result["workspace"] = workspace' in source
    assert 'result["note"]' in source


# ---------------------------------------------------------------------------
# The tab lands in the owning workspace's layout, server-side
# ---------------------------------------------------------------------------


def _workspace_row(user_id: str, path: str) -> dict:
    """The stored layout for one workspace, read back out of the isolated DB."""

    async def read():
        from cptr.models import Workspace

        row = await Workspace.get_by_user_path(user_id, path)
        return dict(row.data or {}) if row else {}

    return asyncio.run(read())


def _browser_tabs(data: dict) -> list[dict]:
    """Every browser tab in a stored workspace layout, across all groups."""
    return [
        tab
        for group in (data.get("groups") or [])
        if isinstance(group, dict)
        for tab in (group.get("tabs") or [])
        if isinstance(tab, dict) and tab.get("type") == "browser"
    ]


def test_the_tab_is_written_into_the_owning_workspace_not_the_users(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The core of the fix: A's chat puts the page in A's tab strip.

    Run against a real SQLite workspace row and the real proxy manager, because
    the whole claim is "the layout of a workspace nobody is looking at was
    written" -- a mock would only prove the mock was called.
    """
    user = "user-1"
    target_ws = str(tmp_path / "workspace-a")
    other_ws = str(tmp_path / "workspace-b")

    sent: list[dict] = []

    async def fake_emit_to_user(uid: str, payload: dict) -> None:
        sent.append({"user_id": uid, **payload})

    monkeypatch.setattr(socket_main, "emit_to_user", fake_emit_to_user)

    # Both workspaces have real layouts: A is the chat's, B is where the user
    # happens to be looking.
    _seed_workspace(user, target_ws)
    _seed_workspace(user, other_ws)

    asyncio.run(
        socket_main.emit_open_browser(
            user,
            "http://localhost:5173/",
            chat_id="chat-1",
            workspace=target_ws,
            label="Demo",
        )
    )

    a_tabs = _browser_tabs(_workspace_row(user, target_ws))
    assert [t["path"] for t in a_tabs] == ["http://localhost:5173/"]
    assert a_tabs[0]["label"] == "Demo"
    assert a_tabs[0]["browserSessionId"]

    # The user's own workspace is untouched -- nothing was switched, nothing moved.
    assert _browser_tabs(_workspace_row(user, other_ws)) == []
    assert _workspace_row(user, other_ws)["groups"][0]["activeTabId"] == "files"

    # …and the event says which workspace it went to, plus the ids the client
    # needs to adopt the tab instead of opening a second one.
    events = [e for e in sent if e["type"] == "open_browser"]
    assert len(events) == 1
    event = events[0]
    assert event["workspace"] == target_ws
    assert event["browser_session_id"] == a_tabs[0]["browserSessionId"]
    assert event["tab_id"] == a_tabs[0]["id"]

    assert [e["type"] for e in sent if e["type"] == "workspace_browser_opened"] == [
        "workspace_browser_opened"
    ]


def test_a_home_chat_creates_no_session_and_no_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no workspace, so there is no tab strip to write into.

    Asserted by the absence of both the session and the tab id: the client uses
    those to decide it has nothing to adopt, and a stale one would leave a tab
    pointing at a session that was never created.
    """
    sent: list[dict] = []

    async def fake_emit_to_user(uid: str, payload: dict) -> None:
        sent.append(payload)

    monkeypatch.setattr(socket_main, "emit_to_user", fake_emit_to_user)

    asyncio.run(socket_main.emit_open_browser("brendan", "http://localhost:5173/"))

    event = sent[0]
    assert event["workspace"] == ""
    assert event["browser_session_id"] == ""
    assert event["tab_id"] == ""
    assert not [e for e in sent if e["type"] == "workspace_browser_opened"]


def test_opening_the_same_page_twice_does_not_stack_two_tabs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The second call reports nothing to adopt, and the layout still has one tab.

    A repeat is the common case (a chat that retries, or two chats in one
    workspace opening the same dev server), and a duplicated tab strip entry for
    one page is the kind of thing that only shows up much later.
    """
    user = "user-1"
    ws = str(tmp_path / "ws")

    first, second = asyncio.run(
        _add_twice(user, ws, "http://localhost:5173/", "Demo")
    )

    assert first[0] and first[1]
    assert second == ("", "")  # nothing new to adopt
    assert len(_browser_tabs(_workspace_row(user, ws))) == 1


async def _add_twice(user: str, ws: str, url: str, label: str):
    return (
        await socket_main._add_browser_tab_to_workspace(user, ws, url, label),
        await socket_main._add_browser_tab_to_workspace(user, ws, url, label),
    )


def test_a_failed_layout_write_does_not_break_the_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page still opens; the client just opens it the old way.

    The tool has already probed the URL and decided to show it, so a workspace
    row that cannot be written must not turn a working page into an error. The
    event still goes out, with empty ids so the client falls back.
    """
    from cptr.models import Workspace

    async def boom(*args, **kwargs):
        raise RuntimeError("no such table: workspaces")

    monkeypatch.setattr(Workspace, "get_by_user_path", staticmethod(boom))

    sent: list[dict] = []

    async def fake_emit_to_user(uid: str, payload: dict) -> None:
        sent.append(payload)

    monkeypatch.setattr(socket_main, "emit_to_user", fake_emit_to_user)

    asyncio.run(
        socket_main.emit_open_browser(
            "brendan", "http://localhost:5173/", workspace="/nonexistent/workspace"
        )
    )

    event = sent[0]
    assert event["url"] == "http://localhost:5173/"
    assert event["browser_session_id"] == ""
    assert event["tab_id"] == ""


# ---------------------------------------------------------------------------
# The client: threads the workspace through, and does not switch
# ---------------------------------------------------------------------------


def test_client_threads_the_workspace_into_the_handler() -> None:
    """The socket handler must pass ``data.workspace`` down.

    This is the exact line the bug lived on: it read ``data.url`` and
    ``data.label`` off the event and silently ignored the workspace sitting
    right there in the same object.
    """
    source = (FRONTEND / "lib" / "stores" / "chat.ts").read_text(encoding="utf-8")
    assert re.search(
        r"void openBrowserFromChat\(\s*data\.url,\s*data\.label,\s*data\.workspace,",
        source,
    ), "open_browser handler still drops data.workspace"


def test_open_local_page_does_not_switch_workspace() -> None:
    """The target workspace is a hint about ownership, never a switch.

    This is the regression guard for the whole fix. If `switchToWorkspace` is
    called from here again, a chat can yank the user out of the workspace they
    are reading in order to show them a page they did not ask for.
    """
    source = (FRONTEND / "lib" / "stores.ts").read_text(encoding="utf-8")
    body = _function_body(source, "export async function openLocalPage")
    assert "switchToWorkspace" not in body, (
        "openLocalPage switches workspace again — a chat can now move the user"
    )
    # A link the user clicked still opens where they are.
    assert "targetWorkspace" in body


def test_a_clicked_link_still_opens_where_the_user_is() -> None:
    """The markdown link path is a user gesture and must not be re-routed.

    `InlineRenderer` passes no target, so the new guard cannot fire for it — but
    that is exactly the kind of thing a later "make links workspace-aware"
    tidy-up would break.
    """
    source = (FRONTEND / "lib" / "components" / "markdown" / "InlineRenderer.svelte").read_text(
        encoding="utf-8"
    )
    assert re.search(r"void openLocalPage\(url\)\s*;", source), (
        "InlineRenderer now passes a workspace; a clicked link is about the page "
        "the user is reading, not the chat's workspace"
    )


def test_the_background_open_is_announced() -> None:
    """A page the user cannot see has to be said out loud.

    Silently adding a tab to a workspace they are not in is the failure mode
    this fix risks introducing: the page exists, the tool reports success, and
    the user sees nothing at all.
    """
    source = (FRONTEND / "lib" / "stores" / "chat.ts").read_text(encoding="utf-8")
    assert "function notifyBackgroundOpen" in source
    assert "chat.notification.browserTitle" in source
    assert "chat.notification.browserBody" in source
    # The handler must actually call it for a workspace that is not on screen.
    handler = _function_body(source, "async function openBrowserFromChat")
    assert "notifyBackgroundOpen(" in handler


def test_the_notification_names_the_workspace_in_every_locale() -> None:
    """`{{workspace}}` is the whole point of the message, so it must interpolate."""
    locales = FRONTEND / "lib" / "i18n" / "locales"
    files = sorted(locales.glob("*.json"))
    assert len(files) >= 10, "lost locale files"
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("chat.notification.browserTitle", "chat.notification.browserBody"):
            assert key in data, f"{path.name} is missing {key}"
        assert "{{workspace}}" in data["chat.notification.browserTitle"]


def _function_body(source: str, signature: str) -> str:
    """The text of one function, from its signature to the next top-level one.

    Brace-counting rather than a regex to the next `export`, so a nested `}` in a
    template literal or an object literal cannot end the body early.
    """
    start = source.index(signature)
    depth = 0
    started = False
    for i in range(start, len(source)):
        char = source[i]
        if char == "{":
            depth += 1
            started = True
        elif char == "}":
            depth -= 1
            if started and depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unterminated function body for {signature!r}")
