"""Tests for the workspace prompt: it shows on the dashboard and opens every chat.

A workspace's prompt is the short "what is this workspace for" description. It
lives in `workspaces.data["prompt"]` (no schema change), is read back by
`GET /api/state/workspace/prompt` for the dashboard card, and is prepended to
the system prompt so it is the first thing a new conversation in that workspace
reads. Design: `notes/NOTES-workspace-prompt.md`.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cptr.models.base import Base
from cptr.utils import db as dbmod

USER = "user-1"
WS = "/tmp/ws"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def workspace_db(tmp_path, monkeypatch):
    """Point cptr's async session factory at a fresh SQLite file."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'workspace-prompt.db'}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _run(_create())
    monkeypatch.setattr(dbmod, "_engine", engine)
    monkeypatch.setattr(dbmod, "_async_session", factory)
    monkeypatch.setattr(dbmod, "get_engine", lambda: engine)
    monkeypatch.setattr(dbmod, "get_session_factory", lambda: factory)
    yield engine


@pytest.fixture
def request_obj():
    """A Request whose auth resolves to USER, with the router's lookups stubbed."""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/__test__",
            "query_string": b"",
            "headers": [(b"cookie", b"cptr_session=test-token")],
            "client": ("127.0.0.1", 0),
            "server": ("test", 0),
            "scheme": "http",
        }
    )


@pytest.fixture
def router(monkeypatch):
    """`cptr.routers.state` with identity and user lookup stubbed out."""
    from types import SimpleNamespace

    from cptr.routers import state

    class Auth:
        username = "tester"
        role = "admin"

    monkeypatch.setattr(state, "_get_user_id", lambda request: _user_id(USER))
    monkeypatch.setattr(
        state, "identity_for_request", lambda request: _identity(SimpleNamespace(home="/tmp"))
    )
    return state


async def _user_id(user_id: str) -> str:
    return user_id


async def _identity(value):
    return value


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_prompt_is_stored_in_workspace_data(workspace_db):
    from cptr.models import Workspace

    async def main():
        await Workspace.upsert(USER, WS, "ws", {"prompt": "  A repo of notes.  ", "tabs": []})
        return await Workspace.get_prompt(USER, WS)

    assert _run(main()) == "A repo of notes."


def test_prompt_is_empty_when_unset_or_blank(workspace_db):
    from cptr.models import Workspace

    assert Workspace.prompt_from(None) == ""

    async def main():
        row = await Workspace.upsert(USER, WS, "ws", {"tabs": []})
        assert Workspace.prompt_from(row) == ""
        await Workspace.upsert(USER, WS, "ws", {"prompt": "   "})
        return await Workspace.get_prompt(USER, WS)

    assert _run(main()) == ""


def test_prompt_matches_a_path_that_is_written_differently(workspace_db, tmp_path):
    """A chat opened as `~/x` and the dashboard's resolved path are one row."""
    from cptr.models import Workspace

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    async def main():
        await Workspace.upsert(USER, str(real), "real", {"prompt": "Symlinked."})
        return await Workspace.get_prompt(USER, str(link) + "/")

    assert _run(main()) == "Symlinked."


# ---------------------------------------------------------------------------
# Injection: the first thing a chat in this workspace reads
# ---------------------------------------------------------------------------


def test_prompt_leads_the_system_prompt(workspace_db, request_obj):
    from cptr.models import Workspace
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        await Workspace.upsert(USER, WS, "ws", {"prompt": "A workspace of physics notes."})
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith("[WORKSPACE PROMPT]\nA workspace of physics notes.")
    # The rest of the prompt still follows it, in the usual order.
    assert "You are Computer (cptr)" in system
    assert system.index("[WORKSPACE PROMPT]") < system.index("You are Computer (cptr)")


def test_no_prompt_leaves_the_system_prompt_untouched(workspace_db, request_obj):
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith("You are Computer (cptr)")
    assert "WORKSPACE PROMPT" not in system


def test_prompt_is_not_duplicated_when_the_template_places_it(workspace_db, request_obj, monkeypatch):
    """A user template with its own {{WORKSPACE_PROMPT}} keeps its position."""
    from cptr.models import Config, Workspace
    from cptr.utils.prompt_templates import load_system_prompt

    template = "Lead.\n\n{{WORKSPACE_PROMPT}}\n\nTail."

    async def fake_config(key, *args, **kwargs):
        if key == "chat.models":
            return {"*": {"params": {"system_prompt": template}}}
        return None

    monkeypatch.setattr(Config, "get", staticmethod(fake_config))

    async def main():
        await Workspace.upsert(USER, WS, "ws", {"prompt": "Placed by hand."})
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith("Lead.\n\n[WORKSPACE PROMPT]\nPlaced by hand.\n\nTail.")
    assert system.count("[WORKSPACE PROMPT]") == 1


def test_prompt_text_is_inserted_literally(workspace_db, request_obj):
    """Braces, unicode and blank lines in the prompt survive as the author wrote them."""
    from cptr.models import Workspace
    from cptr.utils.prompt_templates import load_system_prompt

    text = "Repository of Sanskrit notes — जय श्री राम.\n\nUse {{FILE_TREE}} in examples."

    async def main():
        await Workspace.upsert(USER, WS, "ws", {"prompt": text})
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith(f"[WORKSPACE PROMPT]\n{text}")
    # A placeholder inside the prompt is not expanded: it is the author's text,
    # not a slot in the template.
    assert "{{FILE_TREE}}" in system.split("</cptr_context>")[0]


def test_home_chat_has_no_workspace_prompt(request_obj):
    """The no-workspace chat is untouched: there is no workspace to describe."""
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        return await load_system_prompt(request_obj, "", "", user_id=USER)

    assert "WORKSPACE PROMPT" not in _run(main())


# ---------------------------------------------------------------------------
# The dashboard's endpoints
# ---------------------------------------------------------------------------


def _call(coro):
    return _run(coro)


def test_prompt_round_trip_through_the_router(workspace_db, request_obj, router):
    from cptr.routers.state import WorkspacePromptBody

    saved = _call(router.put_workspace_prompt(request_obj, WorkspacePromptBody(prompt=" Notes. "), path=WS))
    assert saved == {"status": "saved", "path": WS, "prompt": "Notes."}

    fetched = _call(router.get_workspace_prompt(request_obj, path=WS))
    assert fetched == {"path": WS, "prompt": "Notes."}


def test_setting_an_empty_prompt_clears_it(workspace_db, request_obj, router):
    from cptr.routers.state import WorkspacePromptBody

    _call(router.put_workspace_prompt(request_obj, WorkspacePromptBody(prompt="Gone soon."), path=WS))
    _call(router.put_workspace_prompt(request_obj, WorkspacePromptBody(prompt=""), path=WS))
    assert _call(router.get_workspace_prompt(request_obj, path=WS))["prompt"] == ""


def test_saving_tabs_does_not_erase_the_prompt(workspace_db, request_obj, router, monkeypatch):
    """The editor's save PUTs only tabs; the prompt has to survive it."""
    from cptr.routers.state import WorkspacePromptBody

    _call(router.put_workspace_prompt(request_obj, WorkspacePromptBody(prompt="Keep me."), path=WS))
    _call(router.put_workspace_tool_servers(request_obj, _servers(["srv-1"]), path=WS))

    async def main():
        from cptr.models import Workspace

        return await Workspace.get_prompt(USER, WS)

    assert _run(main()) == "Keep me."


def _servers(ids: list[str]):
    from cptr.routers.state import WorkspaceToolServersBody

    return WorkspaceToolServersBody(toolServers=ids)


def _json_request(payload: dict):
    """A Request carrying a JSON body, as starlette sees the autosave's PUT."""
    import json as _json

    from starlette.requests import Request

    body = _json.dumps(payload).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/__test__",
            "query_string": b"",
            "headers": [(b"cookie", b"cptr_session=test-token")],
            "client": ("127.0.0.1", 0),
            "server": ("test", 0),
            "scheme": "http",
        },
        receive,
    )


def test_a_stale_layout_save_cannot_revert_the_prompt(workspace_db, request_obj, router):
    """The autosave echoes the whole workspace it loaded; that echo must not win.

    Seen live: the prompt was saved, the user clicked a tab, and the 300 ms state
    save PUT the copy it had read *before* the edit, reverting it — "it didn't
    save". The layout endpoint owns the layout, not the prompt (B-019).
    """
    from cptr.models import Workspace
    from cptr.routers.state import WorkspacePromptBody

    async def seed():
        await Workspace.upsert(
            USER, WS, "ws", {"groups": [], "prompt": "Old text.", "toolServers": ["srv-1"]}
        )

    _run(seed())

    # What the page read when the workspace loaded: layout *and* the old prompt.
    snapshot = _call(router.get_workspace(request_obj, path=WS))
    assert snapshot["prompt"] == "Old text."

    # The user edits the prompt and saves it.
    _call(router.put_workspace_prompt(request_obj, WorkspacePromptBody(prompt="New text."), path=WS))
    _call(router.put_workspace_tool_servers(request_obj, _servers(["srv-2"]), path=WS))

    # Then a tab click autosaves the whole object it is still holding.
    _call(router.put_workspace(_json_request(snapshot), path=WS))

    assert _call(router.get_workspace_prompt(request_obj, path=WS))["prompt"] == "New text."

    # The tool-server attachment was re-pointed in the same window; the echo
    # carries the old list, and the layout save must not put it back either.
    async def stored_data():
        row = await Workspace.get_by_user_path(USER, WS)
        return (row.data or {}) if row else {}

    assert _run(stored_data()).get("toolServers") == ["srv-2"]
