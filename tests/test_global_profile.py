"""Tests for the global profile: one prompt that opens *every* chat.

The profile is the per-user counterpart of the workspace prompt. It lives in
`user_states.data["profile"]` (no schema change), is read back by
`GET /api/state/profile` for the Settings → General box, and is prepended to the
system prompt *ahead of* the workspace prompt — because it is the one standing
instruction that is true in every workspace and on the home page too. Design:
`notes/NOTES-global-profile.md`.
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
def profile_db(tmp_path, monkeypatch):
    """Point cptr's async session factory at a fresh SQLite file."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'global-profile.db'}", poolclass=NullPool
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

    async def fake_user_id(request):
        return USER

    async def fake_identity(request):
        return SimpleNamespace(home="/tmp")

    monkeypatch.setattr(state, "_get_user_id", fake_user_id)
    monkeypatch.setattr(state, "identity_for_request", fake_identity)
    return state


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


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_profile_is_stored_beside_the_other_preferences(profile_db):
    from cptr.models import UserStates

    async def main():
        await UserStates.save_data(USER, {"theme": "bw", "profile": "  Be terse.  "})
        return await UserStates.get_profile(USER), await UserStates.get_data(USER)

    profile, data = _run(main())
    assert profile == "Be terse."
    assert data["theme"] == "bw", "the profile shares the blob without evicting anything"


def test_profile_is_empty_when_unset_or_blank(profile_db):
    from cptr.models import UserStates

    assert UserStates.profile_from(None) == ""
    assert UserStates.profile_from({"profile": 42}) == ""

    async def main():
        await UserStates.save_data(USER, {"profile": "   "})
        return await UserStates.get_profile(USER)

    assert _run(main()) == ""


def test_profile_is_capped_on_the_way_in():
    from cptr.models import UserStates
    from cptr.models.users import MAX_PROFILE_CHARS

    assert UserStates.clean_profile("x" * (MAX_PROFILE_CHARS + 500)) == "x" * MAX_PROFILE_CHARS
    assert UserStates.clean_profile("  text  ") == "text"


# ---------------------------------------------------------------------------
# Injection: the first thing *every* chat reads
# ---------------------------------------------------------------------------


def test_profile_leads_the_system_prompt(profile_db, request_obj):
    from cptr.models import UserStates
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        await UserStates.save_data(USER, {"profile": "Address me as Brendan."})
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith("[PROFILE]\nAddress me as Brendan.")
    assert "You are Computer (cptr)" in system
    assert system.index("[PROFILE]") < system.index("You are Computer (cptr)")


def test_profile_leads_the_workspace_prompt_on_home_and_in_a_workspace(profile_db, request_obj):
    """Both blocks lead the prompt, so the order between them has to be fixed."""
    from cptr.models import UserStates, Workspace
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        await UserStates.save_data(USER, {"profile": "Global standing orders."})
        await Workspace.upsert(USER, WS, "ws", {"prompt": "A workspace of physics notes."})
        return (
            await load_system_prompt(request_obj, WS, "test-model", user_id=USER),
            await load_system_prompt(request_obj, "", "", user_id=USER),
        )

    in_workspace, home = _run(main())
    assert in_workspace.startswith(
        "[PROFILE]\nGlobal standing orders.\n\n[WORKSPACE PROMPT]\nA workspace of physics notes."
    )
    assert in_workspace.index("[PROFILE]") < in_workspace.index("[WORKSPACE PROMPT]")
    # A chat with no workspace has no workspace prompt — but still has a profile.
    assert home.startswith("[PROFILE]\nGlobal standing orders.")
    assert "WORKSPACE PROMPT" not in home


def test_no_profile_leaves_the_system_prompt_untouched(profile_db, request_obj):
    from cptr.utils.prompt_templates import load_system_prompt

    async def main():
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith("You are Computer (cptr)")
    assert "PROFILE" not in system


def test_profile_is_not_duplicated_when_the_template_places_it(profile_db, request_obj, monkeypatch):
    """A user template with its own {{PROFILE}} keeps its own position."""
    from cptr.models import Config, UserStates
    from cptr.utils.prompt_templates import load_system_prompt

    template = "Lead.\n\n{{PROFILE}}\n\nTail."

    async def fake_config(key, *args, **kwargs):
        if key == "chat.models":
            return {"*": {"params": {"system_prompt": template}}}
        return None

    monkeypatch.setattr(Config, "get", staticmethod(fake_config))

    async def main():
        await UserStates.save_data(USER, {"profile": "Placed by hand."})
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith("Lead.\n\n[PROFILE]\nPlaced by hand.\n\nTail.")
    assert system.count("[PROFILE]") == 1


def test_profile_text_is_inserted_literally(profile_db, request_obj):
    """Braces, unicode and blank lines survive as the author wrote them."""
    from cptr.models import UserStates
    from cptr.utils.prompt_templates import load_system_prompt

    text = "Reply in Hindi — जय श्री राम.\n\nUse {{WORKSPACE_PATH}} in examples."

    async def main():
        await UserStates.save_data(USER, {"profile": text})
        return await load_system_prompt(request_obj, "", "", user_id=USER)

    system = _run(main())
    assert system.startswith(f"[PROFILE]\n{text}")
    # A placeholder inside the profile is not expanded: it is the author's text.
    assert "{{WORKSPACE_PATH}}" in system


def test_a_template_that_places_the_workspace_prompt_still_gets_the_profile_first(
    profile_db, request_obj, monkeypatch
):
    """The profile is prepended to whatever the workspace blocks left in front."""
    from cptr.models import Config, UserStates, Workspace
    from cptr.utils.prompt_templates import load_system_prompt

    async def fake_config(key, *args, **kwargs):
        if key == "chat.models":
            return {"*": {"params": {"system_prompt": "Lead.\n\n{{WORKSPACE_PROMPT}}\n\nTail."}}}
        return None

    monkeypatch.setattr(Config, "get", staticmethod(fake_config))

    async def main():
        await UserStates.save_data(USER, {"profile": "Mine."})
        await Workspace.upsert(USER, WS, "ws", {"prompt": "Placed by hand."})
        return await load_system_prompt(request_obj, WS, "test-model", user_id=USER)

    system = _run(main())
    assert system.startswith("[PROFILE]\nMine.\n\nLead.\n\n[WORKSPACE PROMPT]\nPlaced by hand.")


# ---------------------------------------------------------------------------
# The Settings → General endpoints
# ---------------------------------------------------------------------------


def test_profile_round_trip_through_the_router(profile_db, request_obj, router):
    from cptr.routers.state import ProfileBody

    saved = _run(router.put_profile(request_obj, ProfileBody(profile="  Be terse.  ")))
    assert saved["status"] == "saved"
    assert saved["profile"] == "Be terse."

    fetched = _run(router.get_profile(request_obj))
    assert fetched["profile"] == "Be terse."
    assert fetched["max_chars"] > 0


def test_setting_an_empty_profile_clears_it(profile_db, request_obj, router):
    from cptr.models import UserStates
    from cptr.routers.state import ProfileBody

    _run(router.put_profile(request_obj, ProfileBody(profile="Gone soon.")))
    _run(router.put_profile(request_obj, ProfileBody(profile="")))

    assert _run(router.get_profile(request_obj))["profile"] == ""

    async def stored():
        return await UserStates.get_data(USER)

    assert "profile" not in _run(stored()), "a cleared profile leaves no empty key behind"


def test_a_null_profile_is_read_as_clearing_it(profile_db, request_obj, router):
    """`null` is what a client that has "no value" sends; it must clear, not 422.

    The endpoint's own body model is the only place that decides this, so it is
    validated the way FastAPI would, then handed to the handler.
    """
    from cptr.routers.state import ProfileBody

    _run(router.put_profile(request_obj, ProfileBody(profile="Gone.")))
    _run(router.put_profile(request_obj, ProfileBody.model_validate({"profile": None})))

    assert _run(router.get_profile(request_obj))["profile"] == ""


def test_the_endpoint_caps_an_oversized_profile(profile_db, request_obj, router):
    from cptr.models.users import MAX_PROFILE_CHARS
    from cptr.routers.state import ProfileBody

    saved = _run(router.put_profile(request_obj, ProfileBody(profile="x" * (MAX_PROFILE_CHARS + 10))))
    assert len(saved["profile"]) == MAX_PROFILE_CHARS


def test_the_preferences_autosave_cannot_revert_the_profile(profile_db, request_obj, router):
    """The autosave PUTs the whole prefs blob; a stale one must not win.

    Seen in the workspace case (B-019): a save, then a stale echo half a minute
    later, and the edit is gone. The profile is read by *every* chat, so losing
    it quietly is worse — it gets its own endpoint, and preferences drops the key
    even when the client sends one.
    """
    from cptr.routers.state import ProfileBody

    _run(router.put_profile(request_obj, ProfileBody(profile="New text.")))

    # What a page that loaded the blob before the edit would send back.
    _run(
        router.put_preferences(
            _json_request({"theme": "bw-dark", "profile": "Old text.", "sidebarWidth": 260})
        )
    )

    assert _run(router.get_profile(request_obj))["profile"] == "New text."

    async def prefs():
        from cptr.models import UserStates

        return await UserStates.get_data(USER)

    data = _run(prefs())
    assert data["theme"] == "bw-dark", "the rest of the blob still saved"
    assert data["sidebarWidth"] == 260


def test_the_autosave_that_omits_the_profile_leaves_it_alone(profile_db, request_obj, router):
    """The real client never sends the key at all; the merge has to preserve it."""
    from cptr.routers.state import ProfileBody

    _run(router.put_profile(request_obj, ProfileBody(profile="Keep me.")))
    _run(router.put_preferences(_json_request({"theme": "bw", "sidebarWidth": 240})))

    assert _run(router.get_profile(request_obj))["profile"] == "Keep me."


def test_a_body_that_is_not_an_object_does_not_explode(profile_db, request_obj, router):
    """A malformed preferences PUT must not raise out of the request."""
    assert _run(router.put_preferences(_json_request(["nonsense"]))) == {"status": "saved"}
