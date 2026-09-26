"""Workspace services: declared long-lived processes, one registry per workspace.

A service is a command the user saved on the workspace (an API, a dev server).
Chats start and stop it by name. ``run_command`` is refused when it would launch
that command or bind that port, so a second chat cannot start a second copy.

Runtime state is in memory plus a pid record under the cptr data dir. A clean
shutdown signals every service this process owns. The next boot reattaches a
pid that is still alive and otherwise leaves the service stopped.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from cptr.models.workspaces import Workspace, normalize_path

MAX_SERVICES = 8
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")
_SHELL_TOKENS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<"}
_SERVER_RE = re.compile(
    r"(?i)(?:^|[\s/])(uvicorn|gunicorn|hypercorn|granian|daphne|vite|nuxt|streamlit|gradio|http-server)(?:\s|$)"
    r"|\bhttp\.server\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start)\b"
    r"|\bnext\s+dev\b"
    r"|\bng\s+serve\b"
    r"|\bflask\s+run\b"
)
_GRACE_SECONDS = 5.0
_STOP_WAIT_SECONDS = 3.0
_LOG_CAP = 256 * 1024


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class Runtime:
    service_id: str
    user_id: str
    workspace: str
    pid: int
    pgid: int
    argv: tuple[str, ...]
    cwd: str
    started_at: float
    proc: object | None = None
    master_fd: int | None = None
    output: bytearray = field(default_factory=bytearray)
    owns_reader: bool = False
    session_id: str | None = None
    reader: asyncio.Task | None = None


_runtimes: dict[str, Runtime] = {}
_lock = asyncio.Lock()


def _records_root() -> Path:
    from cptr.env import DATA_DIR

    return Path(DATA_DIR) / "services"


def _record_path(user_id: str, workspace: str, service_id: str) -> Path:
    key = f"{user_id}\0{workspace}".encode()
    import hashlib

    digest = hashlib.sha256(key).hexdigest()[:16]
    return _records_root() / digest / f"{service_id}.json"


def normalize_argv(command: str) -> tuple[str, ...]:
    """Split a command the way the supervisor will exec it."""
    try:
        argv = tuple(shlex.split(command, posix=True))
    except ValueError as exc:
        raise ServiceError(f"command could not be parsed: {exc}") from exc
    if not argv:
        raise ServiceError("command is required")
    if any(tok in _SHELL_TOKENS for tok in argv):
        raise ServiceError("command is an argument list, not a shell pipeline")
    return argv


def canonical_command(command: str) -> str:
    return shlex.join(normalize_argv(command))


def is_server_command(command: str) -> bool:
    return bool(_SERVER_RE.search(command or ""))


def command_uses_port(command: str, port: int | None) -> bool:
    if not port:
        return False
    text = str(port)
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        argv = (command or "").split()
    for index, tok in enumerate(argv):
        if tok == text:
            return True
        if tok in {f"--port={text}", f"-p={text}", f"--port={text}"}:
            return True
        if tok in {"--port", "-p"} and index + 1 < len(argv) and argv[index + 1] == text:
            return True
        if ":" in tok and tok.rsplit(":", 1)[-1] == text:
            return True
    return False


def commands_match(left: str, right: str) -> bool:
    try:
        return normalize_argv(left) == normalize_argv(right)
    except ServiceError:
        return " ".join((left or "").split()) == " ".join((right or "").split())


def pid_alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(errors="replace")
        state = stat[stat.rfind(")") + 1 :].split()[0]
        return state != "Z"
    except (OSError, IndexError):
        return True


def _cmdline(pid: int) -> tuple[str, ...]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ()
    return tuple(part.decode(errors="replace") for part in raw.split(b"\0") if part)


def cmdline_matches(pid: int, argv: tuple[str, ...]) -> bool:
    got = _cmdline(pid)
    if not got or not argv:
        return False
    if got == argv:
        return True
    # A re-exec often replaces argv[0] with an absolute path.
    return len(got) == len(argv) and got[1:] == argv[1:] and Path(got[0]).name == Path(argv[0]).name


def _same_workspace(left: str, right: str) -> bool:
    a = normalize_path(left)
    b = normalize_path(right)
    return bool(a) and a == b


def _resolve_cwd(workspace: str, cwd: str) -> str:
    root = Path(normalize_path(workspace) or workspace)
    raw = (cwd or ".").strip() or "."
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise ServiceError("directory must stay inside the workspace") from exc
    if not resolved.is_dir():
        raise ServiceError(f"directory does not exist: {raw}")
    return str(resolved)


def _display_cwd(workspace: str, cwd: str) -> str:
    """Store a workspace-relative directory when it fits."""
    root = Path(normalize_path(workspace) or workspace).resolve()
    resolved = Path(cwd).resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return str(resolved)
    text = rel.as_posix()
    return text or "."


def _validate_fields(raw: dict, workspace: str) -> dict:
    name = str(raw.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise ServiceError("name must start with a letter and use letters, numbers, _ or -")
    command = canonical_command(str(raw.get("command") or ""))
    if len(command) > 2000:
        raise ServiceError("command is too long")
    cwd = _display_cwd(workspace, _resolve_cwd(workspace, str(raw.get("cwd") or ".")))
    port = raw.get("port")
    if port in ("", None):
        port = None
    else:
        try:
            port = int(port)
        except (TypeError, ValueError) as exc:
            raise ServiceError("port must be a number") from exc
        if port < 1 or port > 65535:
            raise ServiceError("port must be between 1 and 65535")
    health = str(raw.get("health_url") or "").strip()
    if health and not (health.startswith("http://") or health.startswith("https://")):
        raise ServiceError("health URL must start with http:// or https://")
    if len(health) > 500:
        raise ServiceError("health URL is too long")
    return {
        "name": name,
        "command": command,
        "cwd": cwd,
        "port": port,
        "health_url": health,
    }


def _clean_stored(items: object) -> list[dict]:
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if isinstance(item, dict) and item.get("id") and item.get("name") and item.get("command"):
            out.append(dict(item))
    return out


async def _row(user_id: str, workspace: str) -> tuple[Workspace | None, str]:
    path = normalize_path(workspace)
    if not path:
        raise ServiceError("workspace path is required")
    row = await Workspace.get_by_user_path(user_id, path)
    return row, path


async def definitions(user_id: str, workspace: str) -> list[dict]:
    row, _path = await _row(user_id, workspace)
    data = (row.data if row else None) or {}
    return _clean_stored(data.get("services") if isinstance(data, dict) else None)


async def _write(user_id: str, workspace: str, services: list[dict]) -> list[dict]:
    row, path = await _row(user_id, workspace)
    data = dict(row.data or {}) if row else {}
    if services:
        data["services"] = services
    else:
        data.pop("services", None)
    name = row.name if row else (Path(path).name or path)
    store_path = row.path if row else path
    await Workspace.upsert(user_id, store_path, name, data)
    return services


def _find(services: list[dict], service_id: str = "", name: str = "") -> dict | None:
    for spec in services:
        if service_id and spec.get("id") == service_id:
            return spec
        if name and spec.get("name") == name:
            return spec
    return None


async def resolve_id(
    user_id: str, workspace: str, service_id: str = "", name: str = ""
) -> str:
    """Resolve a service to its id by either id or name (raises when missing)."""
    path = normalize_path(workspace)
    spec = _find(await definitions(user_id, path), service_id=service_id, name=name)
    if not spec:
        raise ServiceError("service not found", 404)
    return str(spec.get("id") or "")


async def create_service(user_id: str, workspace: str, raw: dict) -> dict:
    fields = _validate_fields(raw, workspace)
    async with _lock:
        services = await definitions(user_id, workspace)
        if len(services) >= MAX_SERVICES:
            raise ServiceError(f"this workspace already has {MAX_SERVICES} services")
        if _find(services, name=fields["name"]):
            raise ServiceError(f"a service named {fields['name']} already exists")
        spec = {"id": uuid.uuid4().hex[:12], **fields}
        services.append(spec)
        await _write(user_id, workspace, services)
        return spec


async def update_service(user_id: str, workspace: str, service_id: str, raw: dict) -> dict:
    fields = _validate_fields(raw, workspace)
    async with _lock:
        services = await definitions(user_id, workspace)
        spec = _find(services, service_id=service_id)
        if not spec:
            raise ServiceError("service not found", 404)
        other = _find(services, name=fields["name"])
        if other and other.get("id") != service_id:
            raise ServiceError(f"a service named {fields['name']} already exists")
        running = _runtime_for(service_id)
        command_changed = fields["command"] != spec.get("command") or fields["cwd"] != spec.get("cwd")
        if running and _alive(running) and command_changed:
            raise ServiceError("stop the service before changing its command or directory")
        spec.update(fields)
        await _write(user_id, workspace, services)
        return spec


async def delete_service(user_id: str, workspace: str, service_id: str) -> None:
    await stop(user_id, workspace, service_id, missing_ok=True)
    async with _lock:
        services = await definitions(user_id, workspace)
        kept = [spec for spec in services if spec.get("id") != service_id]
        if len(kept) == len(services):
            raise ServiceError("service not found", 404)
        await _write(user_id, workspace, kept)


def _runtime_for(service_id: str) -> Runtime | None:
    runtime = _runtimes.get(service_id)
    if runtime and not _alive(runtime):
        _drop(runtime)
        return None
    return runtime


def _alive(runtime: Runtime) -> bool:
    return pid_alive(runtime.pid)


def _drop(runtime: Runtime) -> None:
    _runtimes.pop(runtime.service_id, None)
    path = _record_path(runtime.user_id, runtime.workspace, runtime.service_id)
    try:
        path.unlink()
    except OSError:
        pass


def _write_record(runtime: Runtime, spec: dict) -> None:
    path = _record_path(runtime.user_id, runtime.workspace, runtime.service_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "service_id": runtime.service_id,
        "user_id": runtime.user_id,
        "workspace": runtime.workspace,
        "name": spec.get("name") or "",
        "pid": runtime.pid,
        "pgid": runtime.pgid,
        "argv": list(runtime.argv),
        "cwd": runtime.cwd,
        "started_at": runtime.started_at,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def _signal(runtime: Runtime, force: bool) -> None:
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(runtime.pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(runtime.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _spawn(argv: tuple[str, ...], cwd: str, env: dict, preexec) -> tuple[object, int | None]:
    import subprocess

    extra: dict = {"start_new_session": True}
    if preexec is not None:
        extra["preexec_fn"] = preexec
    try:
        import fcntl
        import pty
        import struct
        import termios
    except ImportError:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
            **extra,
        )
        return proc, None

    master_fd, slave_fd = pty.openpty()
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        proc = subprocess.Popen(
            list(argv),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            **extra,
        )
    except Exception:
        os.close(slave_fd)
        os.close(master_fd)
        raise
    os.close(slave_fd)
    return proc, master_fd


async def _read_output(runtime: Runtime) -> None:
    loop = asyncio.get_running_loop()
    proc = runtime.proc
    master_fd = runtime.master_fd
    try:
        while True:
            if master_fd is not None:
                try:
                    chunk = await loop.run_in_executor(None, os.read, master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
            else:
                stream = getattr(proc, "stdout", None)
                if stream is None:
                    break
                chunk = await stream.read(4096)
                if not chunk:
                    break
            runtime.output.extend(chunk)
            if len(runtime.output) > _LOG_CAP:
                runtime.output = runtime.output[-_LOG_CAP:]
    except Exception:
        pass
    finally:
        if master_fd is not None:
            if proc is not None:
                await loop.run_in_executor(None, proc.wait)
            try:
                os.close(master_fd)
            except OSError:
                pass
        elif proc is not None:
            await proc.wait()
        runtime.master_fd = None
        _drop(runtime)


async def _env_for(user_id: str, cwd: str) -> tuple[dict, object]:
    from cptr.utils.identity import env_for, identity_for_user_id, preexec_for

    try:
        identity = await identity_for_user_id(user_id)
        return env_for(identity, cwd, {"PAGER": "cat", "GIT_PAGER": "cat"}), preexec_for(identity)
    except Exception:
        env = {**os.environ, "PAGER": "cat", "GIT_PAGER": "cat"}
        return env, None


def _adopt_session(spec: dict, user_id: str, workspace: str, session_id: str, session: dict) -> Runtime:
    proc = session.get("proc")
    pid = int(getattr(proc, "pid", 0) or 0)
    argv = normalize_argv(str(session.get("command") or spec["command"]))
    runtime = Runtime(
        service_id=spec["id"],
        user_id=user_id,
        workspace=workspace,
        pid=pid,
        pgid=pid,
        argv=argv,
        cwd=str(session.get("cwd") or workspace),
        started_at=float(session.get("created_at") or time.time()),
        proc=proc,
        master_fd=None,
        output=bytearray(),
        owns_reader=False,
        session_id=session_id,
    )
    session["service_id"] = spec["id"]
    _runtimes[spec["id"]] = runtime
    _write_record(runtime, spec)
    return runtime


def _matching_session(user_id: str, workspace: str, spec: dict) -> tuple[str, dict] | None:
    from cptr.utils.tools import command_sessions

    for session_id, session in command_sessions.items():
        if session.get("done") or session.get("service_id"):
            continue
        if user_id and session.get("user_id") not in (None, user_id):
            continue
        if not _same_workspace(str(session.get("workspace") or ""), workspace):
            continue
        command = str(session.get("command") or "")
        if commands_match(command, spec["command"]) or command_uses_port(command, spec.get("port")):
            proc = session.get("proc")
            if proc is not None and pid_alive(int(getattr(proc, "pid", 0) or 0)):
                return session_id, session
    return None


async def start(user_id: str, workspace: str, service_id: str = "", name: str = "") -> dict:
    async with _lock:
        _path = normalize_path(workspace)
        services = await definitions(user_id, _path)
        spec = _find(services, service_id=service_id, name=name)
        if not spec:
            raise ServiceError("service not found", 404)
        existing = _runtime_for(spec["id"])
        if existing:
            return await _public(spec, existing)
        adopted = _matching_session(user_id, _path, spec)
        if adopted:
            session_id, session = adopted
            runtime = _adopt_session(spec, user_id, _path, session_id, session)
            return await _public(spec, runtime)

        cwd = _resolve_cwd(_path, str(spec.get("cwd") or "."))
        argv = normalize_argv(str(spec["command"]))
        env, preexec = await _env_for(user_id, cwd)
        try:
            proc, master_fd = _spawn(argv, cwd, env, preexec)
        except OSError as exc:
            raise ServiceError(f"could not start: {exc}") from exc
        runtime = Runtime(
            service_id=spec["id"],
            user_id=user_id,
            workspace=_path,
            pid=proc.pid,
            pgid=proc.pid,
            argv=argv,
            cwd=cwd,
            started_at=time.time(),
            proc=proc,
            master_fd=master_fd,
            owns_reader=True,
        )
        _runtimes[spec["id"]] = runtime
        _write_record(runtime, spec)
        runtime.reader = asyncio.create_task(_read_output(runtime))
        return await _public(spec, runtime)


async def stop(
    user_id: str, workspace: str, service_id: str = "", name: str = "", missing_ok: bool = False
) -> dict:
    async with _lock:
        path = normalize_path(workspace)
        services = await definitions(user_id, path)
        spec = _find(services, service_id=service_id, name=name)
        if not spec:
            if missing_ok:
                return {"status": "stopped"}
            raise ServiceError("service not found", 404)
        runtime = _runtimes.get(spec["id"])
        if runtime is None or not _alive(runtime):
            if runtime:
                _drop(runtime)
            return await _public(spec, None)
        _signal(runtime, force=False)
        deadline = time.time() + _STOP_WAIT_SECONDS
        while time.time() < deadline and pid_alive(runtime.pid):
            await asyncio.sleep(0.1)
        if pid_alive(runtime.pid):
            _signal(runtime, force=True)
        _drop(runtime)
        return await _public(spec, None)


async def restart(user_id: str, workspace: str, service_id: str) -> dict:
    await stop(user_id, workspace, service_id=service_id, missing_ok=True)
    return await start(user_id, workspace, service_id=service_id)


def _logs_of(runtime: Runtime) -> bytes:
    if runtime.session_id:
        from cptr.utils.tools import command_sessions

        session = command_sessions.get(runtime.session_id)
        if session and isinstance(session.get("output"), bytearray):
            return bytes(session["output"])
    return bytes(runtime.output)


async def logs(user_id: str, workspace: str, name: str = "", service_id: str = "") -> str:
    path = normalize_path(workspace)
    spec = _find(await definitions(user_id, path), service_id=service_id, name=name)
    if not spec:
        raise ServiceError("service not found", 404)
    runtime = _runtime_for(spec["id"])
    if not runtime:
        return f"Service {spec['name']} is stopped."
    text = _logs_of(runtime).decode(errors="replace")
    if len(text) > 8000:
        text = text[-8000:]
    status = (await _public(spec, runtime))["status"]
    return f"Service {spec['name']}: {status}\n---\n{text}"


async def _probe(spec: dict, runtime: Runtime | None) -> bool | None:
    if runtime is None:
        return None
    health = str(spec.get("health_url") or "").strip()
    port = spec.get("port")
    if not health and not port:
        return None
    if time.time() - runtime.started_at < _GRACE_SECONDS:
        return None

    def _http() -> bool:
        try:
            with urlopen(health, timeout=1.5) as response:
                response.read(64)
            return True
        except HTTPError:
            return True
        except (URLError, OSError, ValueError):
            return False

    if health:
        return await asyncio.to_thread(_http)
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", int(port)), timeout=0.8
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _url(spec: dict) -> str:
    health = str(spec.get("health_url") or "").strip()
    if health:
        return health
    port = spec.get("port")
    if port:
        return f"http://127.0.0.1:{port}/"
    return ""


async def _public(spec: dict, runtime: Runtime | None) -> dict:
    alive = runtime is not None and _alive(runtime)
    healthy = await _probe(spec, runtime if alive else None)
    if not alive:
        status = "stopped"
    elif healthy is False:
        status = "unhealthy"
    else:
        status = "running"
    return {
        "id": spec.get("id"),
        "name": spec.get("name"),
        "command": spec.get("command"),
        "cwd": spec.get("cwd") or ".",
        "port": spec.get("port"),
        "health_url": spec.get("health_url") or "",
        "status": status,
        "pid": runtime.pid if alive and runtime else None,
        "url": _url(spec),
        "healthy": healthy,
    }


def _unmanaged(user_id: str, workspace: str, owned_pids: set[int]) -> list[dict]:
    from cptr.utils.tools import command_sessions

    rows = []
    for session_id, session in command_sessions.items():
        if session.get("done") or session.get("service_id"):
            continue
        if user_id and session.get("user_id") not in (None, user_id):
            continue
        if not _same_workspace(str(session.get("workspace") or ""), workspace):
            continue
        proc = session.get("proc")
        pid = int(getattr(proc, "pid", 0) or 0)
        if pid in owned_pids or not pid_alive(pid):
            continue
        command = str(session.get("command") or "")
        if not is_server_command(command):
            continue
        rows.append(
            {
                "command_session_id": session_id,
                "command": command,
                "cwd": str(session.get("cwd") or ""),
                "pid": pid,
                "created_at": session.get("created_at") or 0,
            }
        )
    rows.sort(key=lambda row: -float(row["created_at"] or 0))
    return rows


async def snapshot(user_id: str, workspace: str) -> dict:
    path = normalize_path(workspace)
    services = await definitions(user_id, path)
    public = []
    owned: set[int] = set()
    for spec in services:
        runtime = _runtime_for(spec["id"])
        if runtime:
            owned.add(runtime.pid)
        public.append(await _public(spec, runtime))
    return {
        "path": path,
        "services": public,
        "unmanaged": _unmanaged(user_id, path, owned),
    }


async def stop_unmanaged(user_id: str, workspace: str, session_id: str) -> None:
    from cptr.utils.tools import _kill_process_group, command_sessions

    session = command_sessions.get(session_id)
    if not session or (user_id and session.get("user_id") not in (None, user_id)):
        raise ServiceError("process not found", 404)
    if not _same_workspace(str(session.get("workspace") or ""), workspace):
        raise ServiceError("process not found", 404)
    if session.get("done"):
        return
    proc = session.get("proc")
    pid = int(getattr(proc, "pid", 0) or 0)
    if pid:
        _kill_process_group(pid)


async def adopt_unmanaged(user_id: str, workspace: str, session_id: str, raw: dict) -> dict:
    from cptr.utils.tools import command_sessions

    session = command_sessions.get(session_id)
    if not session or session.get("done"):
        raise ServiceError("process not found", 404)
    if user_id and session.get("user_id") not in (None, user_id):
        raise ServiceError("process not found", 404)
    if not _same_workspace(str(session.get("workspace") or ""), workspace):
        raise ServiceError("process not found", 404)
    proc = session.get("proc")
    if proc is None or not pid_alive(int(getattr(proc, "pid", 0) or 0)):
        raise ServiceError("process is not running")
    body = {
        "name": raw.get("name") or "",
        "command": session.get("command") or "",
        "cwd": session.get("cwd") or ".",
        "port": raw.get("port"),
        "health_url": raw.get("health_url") or "",
    }
    spec = await create_service(user_id, workspace, body)
    path = normalize_path(workspace)
    async with _lock:
        _adopt_session(spec, user_id, path, session_id, session)
    return spec


def _match_spec(spec: dict, command: str, cwd: str, workspace: str) -> bool:
    del cwd, workspace
    if commands_match(command, str(spec.get("command") or "")):
        return True
    return command_uses_port(command, spec.get("port"))


async def refusal_for_command(
    user_id: str, workspace: str, command: str, cwd: str = "."
) -> str | None:
    """Why ``run_command`` must not spawn ``command``, or None when it may."""
    if not user_id or not workspace or not (command or "").strip():
        return None
    path = normalize_path(workspace)
    try:
        services = await definitions(user_id, path)
    except Exception:
        return None
    for spec in services:
        if not _match_spec(spec, command, cwd or path, path):
            continue
        runtime = _runtime_for(spec["id"])
        view = await _public(spec, runtime)
        if view["status"] == "stopped":
            return (
                f"Service '{spec['name']}' is declared for this command and is stopped. "
                f"Use start_service(name={spec['name']!r}) to start it. "
                "Do not run the command yourself."
            )
        where = f" at {view['url']}" if view.get("url") else ""
        return (
            f"Service '{spec['name']}' is already {view['status']}{where}. "
            f"Use start_service(name={spec['name']!r}) to reuse it. "
            "Do not start another copy."
        )

    if not is_server_command(command):
        return None
    for row in _unmanaged(user_id, path, set()):
        if commands_match(command, row["command"]):
            return (
                f"That server is already running as task {row['command_session_id']} "
                f"(pid {row['pid']}). Stop it from the workspace dashboard, or kill_task "
                f"{row['command_session_id']}, instead of starting another copy."
            )
    return None


def format_services_prompt(view: dict) -> str:
    services = view.get("services") or []
    unmanaged = view.get("unmanaged") or []
    if not services and not unmanaged:
        return ""
    lines = [
        "[WORKSPACE SERVICES]",
        "These are the long-lived processes for this workspace (the dashboard's "
        "start/stop panel). Start, stop, restart or remove them with "
        "start_service, stop_service, restart_service and delete_service. "
        "Save a new one with create_service. Do not launch the same command "
        "with run_command. (These are NOT scheduled automations and NOT todos.)",
    ]
    for spec in services:
        where = f" {spec['url']}" if spec.get("url") else ""
        sid = f", id {spec['id']}" if spec.get("id") else ""
        lines.append(
            f"- {spec['name']}: {spec['status']}{where} — `{spec['command']}` (cwd {spec.get('cwd') or '.'}{sid})"
        )
    for row in unmanaged:
        lines.append(
            f"- unmanaged task {row['command_session_id']}: `{row['command']}` "
            "(save it as a service on the workspace dashboard, or stop it)"
        )
    return "\n".join(lines)


async def services_prompt(user_id: str | None, workspace: str) -> str:
    if not user_id or not workspace:
        return ""
    try:
        return format_services_prompt(await snapshot(user_id, workspace))
    except Exception:
        return ""


async def reattach_all() -> int:
    """Claim pid records whose process is still the command we started."""
    root = _records_root()
    if not root.is_dir():
        return 0
    claimed = 0
    for path in root.glob("*/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        service_id = str(payload.get("service_id") or "")
        pid = int(payload.get("pid") or 0)
        argv = tuple(payload.get("argv") or [])
        if not service_id or service_id in _runtimes:
            continue
        if not pid_alive(pid) or not cmdline_matches(pid, argv):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        _runtimes[service_id] = Runtime(
            service_id=service_id,
            user_id=str(payload.get("user_id") or ""),
            workspace=str(payload.get("workspace") or ""),
            pid=pid,
            pgid=int(payload.get("pgid") or pid),
            argv=argv,
            cwd=str(payload.get("cwd") or ""),
            started_at=float(payload.get("started_at") or time.time()),
            owns_reader=False,
        )
        claimed += 1
    return claimed


async def shutdown_all() -> None:
    """Stop every service this process knows about."""
    runtimes = list(_runtimes.values())
    if not runtimes:
        return
    for runtime in runtimes:
        _signal(runtime, force=False)
    deadline = time.time() + _STOP_WAIT_SECONDS
    while time.time() < deadline and any(pid_alive(runtime.pid) for runtime in runtimes):
        await asyncio.sleep(0.1)
    for runtime in runtimes:
        if pid_alive(runtime.pid):
            _signal(runtime, force=True)
        reader = runtime.reader
        if reader and not reader.done():
            reader.cancel()
        _drop(runtime)
