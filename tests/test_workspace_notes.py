"""Tests for workspace notes: the dashboard lists them, the agent writes them.

A note is a line stuck on a workspace — "the deploy script moved", "tests/x is
flaky". Both sides write the same list: the human from the workspace dashboard
(`POST /api/state/workspace/notes`) and the agent through `add_workspace_note`.
Both sides read it: the dashboard card and the `[WORKSPACE NOTES]` block that
opens every chat in the workspace. Storage is `workspaces.data["notes"]` (no
schema change). Design: `notes/NOTES-workspace-notes.md`.
"""

from __future__ import annotations

import asyncio
import json

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
        f"sqlite+aiosqlite:///{tmp_path / 'workspace-notes.db'}", poolclass=NullPool
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
            "method": "POST",
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


def test_a_note_is_stored_in_workspace_data(workspace_db):
    from cptr.models import Workspace

    async def main():
        note = await Workspace.add_note(USER, WS, "  The deploy script moved.  ", "human")
        return note, await Workspace.get_notes(USER, WS)

    note, notes = _run(main())
    assert note["text"] == "The deploy script moved."  # trimmed
    assert note["author"] == "human"
    assert note["id"] and note["created_at"] > 0
    assert [n["text"] for n in notes] == ["The deploy script moved."]


def test_notes_keep_insertion_order_and_their_author(workspace_db):
    from cptr.models import Workspace

    async def main():
        await Workspace.add_note(USER, WS, "first", "human")
        await Workspace.add_note(USER, WS, "second", "agent")
        return await Workspace.get_notes(USER, WS)

    notes = _run(main())
    assert [(n["text"], n["author"]) for n in notes] == [
        ("first", "human"),
        ("second", "agent"),
    ]


def test_a_blank_note_is_rejected_and_changes_nothing(workspace_db):
    from cptr.models import Workspace

    async def main():
        with pytest.raises(ValueError):
            await Workspace.add_note(USER, WS, "   ", "human")
        return await Workspace.get_notes(USER, WS)

    assert _run(main()) == []


def test_an_over_long_note_is_rejected(workspace_db):
    from cptr.models import Workspace, workspaces

    async def main():
        with pytest.raises(ValueError):
            await Workspace.add_note(USER, WS, "x" * (workspaces.MAX_NOTE_CHARS + 1), "human")

    _run(main())


def test_the_list_is_capped(workspace_db):
    """`data` is one JSON column and every note is read back into a prompt."""
    from cptr.models import Workspace, workspaces

    async def main():
        for i in range(workspaces.MAX_NOTES):
            await Workspace.add_note(USER, WS, f"note {i}", "human")
        with pytest.raises(ValueError):
            await Workspace.add_note(USER, WS, "one too many", "human")
        return await Workspace.get_notes(USER, WS)

    assert len(_run(main())) == workspaces.MAX_NOTES


def test_an_unknown_author_is_recorded_as_human(workspace_db):
    """Guards the dashboard's "who wrote this" label against a bad payload."""
    from cptr.models import Workspace

    async def main():
        note = await Workspace.add_note(USER, WS, "hello", "somebody-else")
        return note, await Workspace.get_notes(USER, WS)

    note, notes = _run(main())
    assert note["author"] == "human"
    assert notes[0]["author"] == "human"


def test_junk_in_the_data_column_is_ignored(workspace_db):
    """A row written by hand (or by an older build) must not break the list."""
    from cptr.models import Workspace

    async def main():
        await Workspace.upsert(
            USER,
            WS,
            "ws",
            {"notes": ["not a dict", {"text": "   "}, {"text": "real", "author": "agent"}]},
        )
        return await Workspace.get_notes(USER, WS)

    notes = _run(main())
    assert [n["text"] for n in notes] == ["real"]


def test_deleting_a_note_removes_only_that_note(workspace_db):
    from cptr.models import Workspace

    async def main():
        first = await Workspace.add_note(USER, WS, "keep", "human")
        second = await Workspace.add_note(USER, WS, "remove", "agent")
        removed = await Workspace.delete_note(USER, WS, second["id"])
        unknown = await Workspace.delete_note(USER, WS, "no-such-id")
        return removed, unknown, await Workspace.get_notes(USER, WS), first

    removed, unknown, notes, _first = _run(main())
    assert removed is True
    assert unknown is False
    assert [n["text"] for n in notes] == ["keep"]


def test_removing_the_last_note_drops_the_key(workspace_db):
    """An empty list is a key the layout autosave would carry around forever."""
    from cptr.models import Workspace

    async def main():
        note = await Workspace.add_note(USER, WS, "only", "human")
        await Workspace.delete_note(USER, WS, note["id"])
        row = await Workspace.get_by_user_path(USER, WS)
        return (row.data or {}) if row else {}

    assert "notes" not in _run(main())


def test_a_bare_upsert_replaces_the_whole_data_column(workspace_db):
    """Why the router — not the model — has to guard the key (B-019).

    `data` is one JSON column and `upsert` writes it whole, so a save that does
    not carry the notes would drop them: `put_workspace` copies the stored value
    back in for exactly this reason (`test_a_stale_layout_save_cannot_revert_a_note`).
    """
    from cptr.models import Workspace

    async def main():
        await Workspace.add_note(USER, WS, "gone with the layout save", "human")
        await Workspace.upsert(USER, WS, "ws", {"groups": [], "tabs": ["files"]})
        return await Workspace.get_notes(USER, WS)

    assert _run(main()) == []


def test_notes_match_a_path_that_is_written_differently(workspace_db, tmp_path):
    """A chat opened as `~/x` and the dashboard's resolved path are one row."""
    from cptr.models import Workspace

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    async def main():
        await Workspace.add_note(USER, str(real), "Symlinked.", "human")
        return await Workspace.get_notes(USER, str(link) + "/")

    assert [n["text"] for n in _run(main())] == ["Symlinked."]


# ---------------------------------------------------------------------------
# Injection: what every chat in the workspace opens with
# ---------------------------------------------------------------------------


def test_notes_follow_the_prompt_in_the_system_prompt(workspace_db, request_obj):
    from cptr.models import Workspace
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        await Workspace.upsert(USER, WS, "ws", {"prompt": "A workspace of physics notes."})
        await Workspace.add_note(USER, WS, "The deploy script moved.", "human")
        await Workspace.add_note(USER, WS, "tests/test_x is flaky.", "agent")
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert "[WORKSPACE NOTES]" in system
    assert "- (agent) tests/test_x is flaky." in system  # newest first
    assert "- (human) The deploy script moved." in system
    # After the description of the workspace, before the standing instructions.
    assert system.index("[WORKSPACE PROMPT]") < system.index("[WORKSPACE NOTES]")
    assert system.index("[WORKSPACE NOTES]") < system.index("You are Computer (cptr)")


def test_no_notes_leaves_the_system_prompt_untouched(workspace_db, request_obj):
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert "WORKSPACE NOTES" not in system


def test_the_block_shows_the_newest_notes_and_counts_the_rest():
    from cptr.utils.prompt_templates import MAX_NOTES_IN_PROMPT, format_workspace_notes

    notes = [
        {"id": str(i), "text": f"note {i}", "author": "human", "created_at": i}
        for i in range(MAX_NOTES_IN_PROMPT + 3)
    ]
    block = format_workspace_notes(notes)
    assert f"- (human) note {MAX_NOTES_IN_PROMPT + 2}" in block
    assert "note 0" not in block
    assert block.endswith("(3 older notes not shown.)")


def test_a_very_long_note_is_truncated_in_the_prompt():
    from cptr.utils.prompt_templates import MAX_NOTE_CHARS_IN_PROMPT, format_workspace_notes

    block = format_workspace_notes(
        [{"id": "1", "text": "x" * 5000, "author": "human", "created_at": 1}]
    )
    body = block.splitlines()[-1]
    assert body.endswith("…")
    assert len(body) < MAX_NOTE_CHARS_IN_PROMPT + 20


def test_a_template_that_places_the_notes_keeps_its_position(
    workspace_db, request_obj, monkeypatch
):
    from cptr.models import Config, Workspace
    from cptr.utils.prompt_templates import load_system_prompt

    template = "Lead.\n\n{{WORKSPACE_NOTES}}\n\nTail."

    async def fake_config(key, *args, **kwargs):
        if key == "chat.models":
            return {"*": {"params": {"system_prompt": template}}}
        return None

    monkeypatch.setattr(Config, "get", staticmethod(fake_config))

    async def main():
        await Workspace.add_note(USER, WS, "Placed by hand.", "human")
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith("Lead.\n\n[WORKSPACE NOTES]")
    assert system.count("[WORKSPACE NOTES]") == 1


def test_home_chat_has_no_workspace_notes(request_obj):
    """The no-workspace chat is untouched: there is no workspace to pin notes on."""
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        return await load_system_prompt(request_obj, "", "", user_id=USER)

    assert "WORKSPACE NOTES" not in _run(main())


# ---------------------------------------------------------------------------
# The dashboard's endpoints
# ---------------------------------------------------------------------------


def test_notes_round_trip_through_the_router(workspace_db, request_obj, router):
    from cptr.routers.state import WorkspaceNoteBody

    added = _run(
        router.post_workspace_note(request_obj, WorkspaceNoteBody(text=" Pin me. "), path=WS)
    )
    assert added["status"] == "saved"
    assert added["note"]["text"] == "Pin me."
    assert added["note"]["author"] == "human"  # the dashboard is the human
    assert [n["text"] for n in added["notes"]] == ["Pin me."]

    fetched = _run(router.get_workspace_notes(request_obj, path=WS))
    assert fetched == {"path": WS, "notes": added["notes"]}

    removed = _run(router.delete_workspace_note(request_obj, added["note"]["id"], path=WS))
    assert removed["status"] == "deleted"
    assert removed["notes"] == []


def test_an_empty_note_is_a_400_and_a_missing_one_is_a_404(workspace_db, request_obj, router):
    from fastapi import HTTPException

    from cptr.routers.state import WorkspaceNoteBody

    with pytest.raises(HTTPException) as blank:
        _run(router.post_workspace_note(request_obj, WorkspaceNoteBody(text="  "), path=WS))
    assert blank.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        _run(router.delete_workspace_note(request_obj, "no-such-id", path=WS))
    assert missing.value.status_code == 404


def test_a_stale_layout_save_cannot_revert_a_note(workspace_db, request_obj, router):
    """The layout autosave echoes the whole workspace it loaded; that echo must
    not win. The layout endpoint owns the layout, not the notes (B-019)."""
    from cptr.models import Workspace
    from cptr.routers.state import WorkspaceNoteBody

    async def seed():
        await Workspace.upsert(
            USER,
            WS,
            "ws",
            {
                "groups": [],
                "notes": [
                    {"id": "old", "text": "Old note.", "author": "human", "created_at": 1}
                ],
            },
        )

    _run(seed())

    # What the page read when the workspace loaded: layout *and* the old notes.
    snapshot = _run(router.get_workspace(request_obj, path=WS))
    assert [n["text"] for n in snapshot["notes"]] == ["Old note."]

    added = _run(
        router.post_workspace_note(request_obj, WorkspaceNoteBody(text="New note."), path=WS)
    )

    # Then a tab click autosaves the whole object it is still holding.
    _run(router.put_workspace(_json_request(snapshot), path=WS))

    assert [n["text"] for n in _run(router.get_workspace_notes(request_obj, path=WS))["notes"]] == [
        "Old note.",
        "New note.",
    ]

    # …and the reverse: deleting one through the endpoint is not undone either.
    _run(router.delete_workspace_note(request_obj, "old", path=WS))
    _run(router.put_workspace(_json_request(snapshot), path=WS))
    assert [n["text"] for n in _run(router.get_workspace_notes(request_obj, path=WS))["notes"]] == [
        "New note."
    ]
    assert added["note"]["id"] not in {"old"}


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


def test_an_anonymous_caller_cannot_write(workspace_db, request_obj, router, monkeypatch):
    from fastapi import HTTPException

    from cptr.routers.state import WorkspaceNoteBody

    monkeypatch.setattr(router, "_get_user_id", lambda request: _anon())
    with pytest.raises(HTTPException) as exc:
        _run(router.post_workspace_note(request_obj, WorkspaceNoteBody(text="hi"), path=WS))
    assert exc.value.status_code == 403


async def _anon():
    return ""


# ---------------------------------------------------------------------------
# The agent's tools
# ---------------------------------------------------------------------------


def _ctx():
    return {"workspace": WS, "user_id": USER}


def test_add_workspace_note_tool_writes_an_agent_note(workspace_db):
    from cptr.models import Workspace
    from cptr.utils.tools import add_workspace_note

    async def main():
        result = json.loads(await add_workspace_note("The build is at dist/.", __context__=_ctx()))
        return result, await Workspace.get_notes(USER, WS)

    result, notes = _run(main())
    assert result["status"] == "added"
    assert [(n["text"], n["author"]) for n in notes] == [("The build is at dist/.", "agent")]


def test_add_workspace_note_tool_reports_a_bad_note(workspace_db):
    from cptr.utils.tools import add_workspace_note

    async def main():
        return json.loads(await add_workspace_note("   ", __context__=_ctx()))

    assert "error" in _run(main())


def test_list_workspace_notes_tool_reads_them_newest_first(workspace_db):
    from cptr.models import Workspace
    from cptr.utils.tools import list_workspace_notes

    async def main():
        await Workspace.add_note(USER, WS, "older", "human")
        await Workspace.add_note(USER, WS, "newer", "agent")
        return json.loads(await list_workspace_notes(__context__=_ctx()))

    payload = _run(main())
    assert payload["workspace"] == WS
    assert [n["text"] for n in payload["notes"]] == ["newer", "older"]


def test_the_note_tools_run_without_approval():
    """A note is an observation, not a claim about work — nothing to confirm."""
    from cptr.utils.tools import BUILTIN_TOOL_GROUPS, TOOLS

    assert TOOLS["add_workspace_note"]["approval"] == "allow"
    assert TOOLS["list_workspace_notes"]["approval"] == "allow"
    assert BUILTIN_TOOL_GROUPS["notes"] == ("list_workspace_notes", "add_workspace_note")


def test_a_home_chat_has_no_note_tools():
    from cptr.utils.tools import BUILTIN_TOOL_GROUPS, GLOBAL_CHAT_DISABLED_TOOLS

    assert set(BUILTIN_TOOL_GROUPS["notes"]) <= GLOBAL_CHAT_DISABLED_TOOLS


def test_the_notes_group_can_be_switched_off():
    from cptr.utils.tools import is_builtin_tool_enabled, disabled_builtin_tool_names

    assert disabled_builtin_tool_names({"notes": False}) == {
        "list_workspace_notes",
        "add_workspace_note",
    }
    assert not is_builtin_tool_enabled("add_workspace_note", {"notes": False})
    assert is_builtin_tool_enabled("add_workspace_note", {"notes": True})
