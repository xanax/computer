"""Workspace service registry: declare, reuse, and refuse a second copy."""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cptr.models.base import Base
from cptr.utils import db as dbmod

USER = "user-1"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def workspace_db(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'services.db'}", poolclass=NullPool
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
def data_dir(tmp_path, monkeypatch):
    from cptr import env

    root = tmp_path / "cptr-data"
    root.mkdir()
    monkeypatch.setattr(env, "DATA_DIR", root)
    return root


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return str(path)


def test_command_and_port_matching():
    from cptr.utils.services import command_uses_port, commands_match, is_server_command

    assert commands_match("uvicorn app:app --port 8000", "uvicorn  app:app   --port 8000")
    assert not commands_match("uvicorn app:app --port 8000", "uvicorn app:app --port 8001")
    assert command_uses_port("python -m http.server 8765", 8765)
    assert command_uses_port("uvicorn app:app --port 8000", 8000)
    assert not command_uses_port("uvicorn app:app --port 8000", 80)
    assert is_server_command("python -m http.server 8765")
    assert is_server_command("npm run dev")
    assert not is_server_command("git status")
    assert not is_server_command("npm test")


def test_declared_command_is_refused(workspace_db, workspace):
    from cptr.utils.services import create_service, refusal_for_command

    async def main():
        await create_service(
            USER,
            workspace,
            {"name": "api", "command": "sleep 30", "cwd": ".", "port": 8000},
        )
        same = await refusal_for_command(USER, workspace, "sleep 30", cwd=workspace)
        other_port = await refusal_for_command(
            USER, workspace, "uvicorn app:app --port 8000", cwd=workspace
        )
        allowed = await refusal_for_command(USER, workspace, "git status", cwd=workspace)
        return same, other_port, allowed

    same, other_port, allowed = _run(main())
    assert same is not None and "start_service" in same and "api" in same
    assert other_port is not None and "api" in other_port
    assert allowed is None


def test_running_server_command_is_refused(workspace_db, workspace):
    from cptr.utils.tools import command_sessions
    from cptr.utils.services import refusal_for_command

    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    command_sessions["sess1"] = {
        "done": False,
        "command": "python -m http.server 8765",
        "workspace": workspace,
        "user_id": USER,
        "proc": proc,
        "cwd": workspace,
        "created_at": 1,
    }
    try:
        blocked = _run(
            refusal_for_command(USER, workspace, "python -m http.server 8765", cwd=workspace)
        )
        assert blocked is not None
        assert "sess1" in blocked
    finally:
        command_sessions.pop("sess1", None)
        proc.kill()
        proc.wait(timeout=5)


def test_start_reuses_and_stop_kills(workspace_db, data_dir, workspace):
    from cptr.utils.services import create_service, pid_alive, shutdown_all, start, stop

    async def main():
        spec = await create_service(
            USER, workspace, {"name": "nap", "command": "sleep 30", "cwd": "."}
        )
        first = await start(USER, workspace, service_id=spec["id"])
        second = await start(USER, workspace, name="nap")
        assert first["pid"] == second["pid"]
        assert first["status"] == "running"
        assert pid_alive(first["pid"])
        stopped = await stop(USER, workspace, name="nap")
        return first["pid"], stopped["status"]

    try:
        pid, status = _run(main())
    finally:
        _run(shutdown_all())
    assert status == "stopped"
    assert not pid_alive(pid)


def test_reattach_then_shutdown_stops_the_process(data_dir, workspace):
    from cptr.utils.services import (
        _record_path,
        pid_alive,
        reattach_all,
        shutdown_all,
    )

    proc = subprocess.Popen(["sleep", "30"], start_new_session=True, stdout=subprocess.DEVNULL)
    try:
        path = _record_path(USER, workspace, "svc1")
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "service_id": "svc1",
                    "user_id": USER,
                    "workspace": workspace,
                    "pid": proc.pid,
                    "pgid": proc.pid,
                    "argv": ["sleep", "30"],
                    "cwd": workspace,
                    "started_at": 1,
                }
            ),
            encoding="utf-8",
        )

        async def main():
            claimed = await reattach_all()
            await shutdown_all()
            return claimed

        assert _run(main()) == 1
        assert not pid_alive(proc.pid)
    finally:
        if pid_alive(proc.pid):
            proc.kill()
            proc.wait(timeout=5)


def test_tab_save_keeps_services(workspace_db, workspace, monkeypatch):
    from types import SimpleNamespace

    from starlette.requests import Request

    from cptr.models import Workspace
    from cptr.routers import state

    async def _user_id(_request):
        return USER

    async def _identity(_request):
        return SimpleNamespace(home="/tmp")

    monkeypatch.setattr(state, "_get_user_id", _user_id)
    monkeypatch.setattr(state, "identity_for_request", _identity)

    body = json.dumps({"name": "ws", "tabs": [{"id": "t"}]}).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/api/state/workspace",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("test", 0),
            "scheme": "http",
        },
        receive,
    )

    async def main():
        await Workspace.upsert(
            USER,
            workspace,
            "ws",
            {"tabs": [], "services": [{"id": "abc", "name": "api", "command": "sleep 1"}]},
        )
        await state.put_workspace(request, path=workspace)
        row = await Workspace.get_by_user_path(USER, workspace)
        return (row.data or {}).get("services"), (row.data or {}).get("tabs")

    services, tabs = _run(main())
    assert services[0]["name"] == "api"
    assert tabs == [{"id": "t"}]


def test_services_open_the_system_prompt(workspace_db, workspace):
    from cptr.utils.prompt_templates import load_system_prompt
    from cptr.utils.services import create_service
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("test", 0),
            "scheme": "http",
        }
    )

    async def main():
        await create_service(
            USER, workspace, {"name": "api", "command": "uvicorn app:app --port 8000", "cwd": "."}
        )
        return await load_system_prompt(request, workspace, "test-model", user_id=USER)

    system = _run(main())
    assert "[WORKSPACE SERVICES]" in system
    assert "api:" in system
    assert "start_service" in system


def test_run_command_refuses_a_declared_service(workspace_db, workspace, monkeypatch):
    from cptr.utils.config import AuthMode
    from cptr.utils.services import create_service
    from cptr.utils.tools import run_command

    monkeypatch.setattr("cptr.utils.identity.get_auth_mode", lambda: AuthMode.PASSWORD)

    async def main():
        await create_service(
            USER,
            workspace,
            {"name": "api", "command": "uvicorn app:app --port 8000", "port": 8000, "cwd": "."},
        )
        return await run_command(
            "uvicorn app:app --port 8000",
            __context__={"workspace": workspace, "user_id": USER},
        )

    text = _run(main())
    assert "start_service" in text
    assert "api" in text
