"""Server-side persistent state for cptr.

Three layers:
  - preferences: global user prefs in user_states table
  - workspaces: per-workspace state in workspaces table (one row per user+path)
  - active workspace: determined by URL query param, not stored server-side
"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from cptr.env import DATA_DIR
from cptr.models import Chat, UserStates, Workspace
from cptr.utils.config import get_or_create_user
from cptr.utils.identity import IdentityUnavailable, identity_for_request

router = APIRouter(prefix="/api/state", tags=["state"])


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


async def _get_user_id(request: Request) -> str | None:
    auth = getattr(request.state, "auth", None)
    if not auth or not auth.username:
        return None
    return await get_or_create_user(auth.username)


def _resolve_workspace_path(path: str, home: str | None = None) -> str:
    """Return the absolute, normalized workspace path."""
    raw = (path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="workspace path is required")
    if home and raw == "~":
        expanded = Path(home)
    elif home and raw.startswith("~/"):
        expanded = Path(home) / raw[2:]
    else:
        expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise HTTPException(status_code=400, detail="workspace path must be absolute")
    return str(expanded.resolve())


async def _resolve_request_workspace_path(request: Request, path: str) -> str:
    try:
        identity = await identity_for_request(request)
        return _resolve_workspace_path(path, identity.home)
    except IdentityUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _try_resolve_workspace_path(path: str) -> str | None:
    try:
        return _resolve_workspace_path(path)
    except HTTPException:
        return None


def _workspace_time(workspace: Workspace) -> int:
    return workspace.updated_at or workspace.created_at or 0


def _dedupe_workspaces(workspaces: list[Workspace]) -> list[tuple[str, Workspace]]:
    by_path: dict[str, Workspace] = {}
    for workspace in workspaces:
        path = _try_resolve_workspace_path(workspace.path)
        if not path:
            continue
        current = by_path.get(path)
        if current is None or _workspace_time(workspace) > _workspace_time(current):
            by_path[path] = workspace
    return list(by_path.items())


async def _workspaces_at_path(user_id: str, path: str) -> list[Workspace]:
    workspaces = await Workspace.get_by_user(user_id)
    return [
        workspace for workspace in workspaces if _try_resolve_workspace_path(workspace.path) == path
    ]


def _newest_workspace(workspaces: list[Workspace]) -> Workspace | None:
    if not workspaces:
        return None
    return max(workspaces, key=_workspace_time)


def _workspace_display_name(path: str) -> str:
    name = Path(path).name
    if name == path and "\\" in path:
        name = PureWindowsPath(path).name
    return name or path


async def _workspace_summaries(user_id: str) -> list[dict[str, str | int]]:
    workspaces = await Workspace.get_by_user(user_id)
    summaries = _dedupe_workspaces(workspaces)
    from cptr.utils.chat_task import get_active_chat_ids

    unread_counts = await Chat.unread_counts_by_workspace(
        user_id, [path for path, _ in summaries], get_active_chat_ids()
    )
    return [
        {
            "path": path,
            "name": workspace.name or _workspace_display_name(path),
            "unread_count": unread_counts.get(path, 0),
        }
        for path, workspace in summaries
    ]


# ── Preferences ──────────────────────────────────────────────────


@router.get("/preferences")
async def get_preferences(request: Request):
    """Return global user preferences (theme, locale, etc.)."""
    user_id = await _get_user_id(request)
    if not user_id:
        return {}
    return await UserStates.get_data(user_id)


@router.put("/preferences")
async def put_preferences(request: Request):
    """Save global user preferences."""
    user_id = await _get_user_id(request)
    if not user_id:
        return {"status": "skipped"}
    body = await request.json()
    current = await UserStates.get_data(user_id)
    await UserStates.save_data(user_id, {**current, **body})
    return {"status": "saved"}


# ── Workspace list ───────────────────────────────────────────────


@router.get("/workspaces")
async def get_workspace_list(request: Request):
    """Return list of all workspace summaries for the sidebar."""
    user_id = await _get_user_id(request)
    if not user_id:
        return []
    return await _workspace_summaries(user_id)


# ── Single workspace CRUD ────────────────────────────────────────


@router.get("/workspace")
async def get_workspace(request: Request, path: str = Query(...)):
    """Return full state for a single workspace."""
    user_id = await _get_user_id(request)
    if not user_id:
        return {}
    workspace_path = await _resolve_request_workspace_path(request, path)
    workspace = _newest_workspace(await _workspaces_at_path(user_id, workspace_path))
    if not workspace:
        return {
            "name": _workspace_display_name(workspace_path),
            "path": workspace_path,
        }
    workspace_data = dict(workspace.data or {})
    file_browser_cwd = workspace_data.get("fileBrowserCwd")
    if isinstance(file_browser_cwd, str):
        file_browser_path = _try_resolve_workspace_path(file_browser_cwd)
        workspace_data["fileBrowserCwd"] = file_browser_path or workspace_path
    return {
        **workspace_data,
        "name": workspace.name or _workspace_display_name(workspace_path),
        "path": workspace_path,
    }


@router.put("/workspace")
async def put_workspace(request: Request, path: str = Query(...)):
    """Create or update a single workspace's state."""
    user_id = await _get_user_id(request)
    if not user_id:
        return {"status": "skipped"}
    workspace_path = await _resolve_request_workspace_path(request, path)
    workspace_data = await request.json()
    existing_workspace = _newest_workspace(await _workspaces_at_path(user_id, workspace_path))
    if "name" in workspace_data:
        name = workspace_data.pop("name")
    else:
        name = (
            existing_workspace.name
            if existing_workspace
            else _workspace_display_name(workspace_path)
        )
    workspace_data.pop("path", None)
    # This save owns the tab layout. The prompt, the tool-server attachments and
    # the declared services each have their own endpoint — and the client's
    # layout autosave echoes back *everything* it read when the workspace loaded,
    # so a key that is present but stale used to win and undo the edit the user
    # had just made: save a prompt, touch any tab, and the 300 ms state save PUT
    # the copy it had read before the edit, reverting it (B-019). A guard that
    # only fired for *absent* keys could not see that, because the echo supplies
    # the key. The stored value always wins here; only the dedicated endpoints
    # may change these three.
    existing_data = (existing_workspace.data or {}) if existing_workspace else {}
    for key in ("toolServers", "prompt", "services"):
        workspace_data.pop(key, None)
        if key in existing_data:
            workspace_data[key] = existing_data[key]
    # Everything else is workspace data (groups, tabs, etc.)
    await Workspace.upsert(user_id, workspace_path, name, workspace_data)

    old_paths = [
        workspace.path
        for workspace in await _workspaces_at_path(user_id, workspace_path)
        if workspace.path != workspace_path
    ]
    await Workspace.delete_by_paths(user_id, old_paths)

    return {"status": "saved", "path": workspace_path}


class WorkspaceToolServersBody(BaseModel):
    toolServers: list[str] = []


@router.get("/tool-servers")
async def list_visible_tool_servers(request: Request):
    """List registered tool servers without secrets, for workspace attachment."""
    if not await _get_user_id(request):
        return {"servers": []}
    from cptr.models import Config

    servers = await Config.get("tool_servers") or []
    out = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        out.append(
            {
                "id": server.get("id") or "",
                "name": server.get("name") or server.get("id") or "",
                "description": server.get("description") or "",
                "type": server.get("type") or "openapi",
                "scope": server.get("scope") or "global",
                "enabled": server.get("enabled", True),
            }
        )
    return {"servers": out}


@router.put("/workspace/tool-servers")
async def put_workspace_tool_servers(
    request: Request, body: WorkspaceToolServersBody, path: str = Query(...)
):
    """Attach a subset of tool servers to one workspace without touching tabs."""
    user_id = await _get_user_id(request)
    if not user_id:
        return {"status": "skipped"}
    workspace_path = await _resolve_request_workspace_path(request, path)
    existing = _newest_workspace(await _workspaces_at_path(user_id, workspace_path))
    data = dict(existing.data or {}) if existing else {}
    ids = [s for s in body.toolServers if isinstance(s, str) and s.strip()]
    data["toolServers"] = ids
    name = existing.name if existing else _workspace_display_name(workspace_path)
    await Workspace.upsert(user_id, workspace_path, name, data)
    return {"status": "saved", "path": workspace_path, "toolServers": ids}


class WorkspacePromptBody(BaseModel):
    prompt: str = ""


@router.get("/workspace/prompt")
async def get_workspace_prompt(request: Request, path: str = Query(...)):
    """Return the workspace's own description (the block its chats open with)."""
    user_id = await _get_user_id(request)
    if not user_id:
        return {"path": path, "prompt": ""}
    workspace_path = await _resolve_request_workspace_path(request, path)
    workspace = _newest_workspace(await _workspaces_at_path(user_id, workspace_path))
    return {"path": workspace_path, "prompt": Workspace.prompt_from(workspace)}


@router.put("/workspace/prompt")
async def put_workspace_prompt(
    request: Request, body: WorkspacePromptBody, path: str = Query(...)
):
    """Set the workspace's description without touching tabs or tool servers."""
    user_id = await _get_user_id(request)
    if not user_id:
        return {"status": "skipped"}
    workspace_path = await _resolve_request_workspace_path(request, path)
    existing = _newest_workspace(await _workspaces_at_path(user_id, workspace_path))
    data = dict(existing.data or {}) if existing else {}
    prompt = body.prompt.strip() if isinstance(body.prompt, str) else ""
    if prompt:
        data["prompt"] = prompt
    else:
        data.pop("prompt", None)
    name = existing.name if existing else _workspace_display_name(workspace_path)
    await Workspace.upsert(user_id, workspace_path, name, data)
    return {"status": "saved", "path": workspace_path, "prompt": prompt}


class WorkspaceServiceBody(BaseModel):
    name: str
    command: str
    cwd: str = "."
    port: int | None = None
    health_url: str = ""


class AdoptServiceBody(BaseModel):
    name: str
    port: int | None = None
    health_url: str = ""


def _service_error(exc: Exception) -> HTTPException:
    from cptr.utils.services import ServiceError

    if isinstance(exc, ServiceError):
        return HTTPException(status_code=exc.status_code, detail=exc.message)
    raise exc


@router.get("/workspace/services")
async def get_workspace_services(request: Request, path: str = Query(...)):
    """Declared services and unmanaged servers for one workspace."""
    from cptr.utils.services import snapshot

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    return await snapshot(user_id, workspace_path)


@router.post("/workspace/services")
async def post_workspace_service(request: Request, body: WorkspaceServiceBody, path: str = Query(...)):
    """Save a service definition. Does not start it."""
    from cptr.utils.services import ServiceError, create_service

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    try:
        spec = await create_service(user_id, workspace_path, body.model_dump())
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return spec


@router.put("/workspace/services/{service_id}")
async def put_workspace_service(
    request: Request, service_id: str, body: WorkspaceServiceBody, path: str = Query(...)
):
    from cptr.utils.services import ServiceError, update_service

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    try:
        spec = await update_service(user_id, workspace_path, service_id, body.model_dump())
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return spec


@router.delete("/workspace/services/{service_id}")
async def delete_workspace_service(request: Request, service_id: str, path: str = Query(...)):
    from cptr.utils.services import ServiceError, delete_service

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    try:
        await delete_service(user_id, workspace_path, service_id)
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return {"status": "deleted", "id": service_id}


@router.post("/workspace/services/{service_id}/start")
async def start_workspace_service(request: Request, service_id: str, path: str = Query(...)):
    from cptr.utils.services import ServiceError, start

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    try:
        return await start(user_id, workspace_path, service_id=service_id)
    except ServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/workspace/services/{service_id}/stop")
async def stop_workspace_service(request: Request, service_id: str, path: str = Query(...)):
    from cptr.utils.services import ServiceError, stop

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    try:
        return await stop(user_id, workspace_path, service_id=service_id)
    except ServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/workspace/services/{service_id}/restart")
async def restart_workspace_service(request: Request, service_id: str, path: str = Query(...)):
    from cptr.utils.services import ServiceError, restart

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    try:
        return await restart(user_id, workspace_path, service_id)
    except ServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/workspace/services/unmanaged/{session_id}/stop")
async def stop_unmanaged_service(request: Request, session_id: str, path: str = Query(...)):
    from cptr.utils.services import ServiceError, stop_unmanaged

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    try:
        await stop_unmanaged(user_id, workspace_path, session_id)
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return {"status": "stopped", "command_session_id": session_id}


@router.post("/workspace/services/unmanaged/{session_id}/adopt")
async def adopt_unmanaged_service(
    request: Request, session_id: str, body: AdoptServiceBody, path: str = Query(...)
):
    from cptr.utils.services import ServiceError, adopt_unmanaged

    user_id = await _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="sign in required")
    workspace_path = await _resolve_request_workspace_path(request, path)
    try:
        spec = await adopt_unmanaged(
            user_id,
            workspace_path,
            session_id,
            body.model_dump(),
        )
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return spec


@router.delete("/workspace")
async def delete_workspace(request: Request, path: str = Query(...)):
    """Remove a workspace."""
    user_id = await _get_user_id(request)
    if not user_id:
        return {"status": "skipped"}
    workspace_path = await _resolve_request_workspace_path(request, path)
    paths = [workspace.path for workspace in await _workspaces_at_path(user_id, workspace_path)]
    if workspace_path not in paths:
        paths.append(workspace_path)
    await Workspace.delete_by_paths(user_id, paths)
    return {"status": "deleted"}


# ── Welcome ──────────────────────────────────────────────────────


@router.get("/welcome")
async def get_welcome(request: Request):
    """Return data for the welcome/landing page."""
    import asyncio

    try:
        identity = await identity_for_request(request)
        user_home = Path(identity.home)
    except Exception:
        user_home = Path.home()

    system_info = await asyncio.to_thread(_collect_system_info, user_home)

    # Recent workspaces from DB (most recently used first)
    user_id = await _get_user_id(request)
    recent: list[dict] = []
    if user_id:
        recents = sorted(
            _dedupe_workspaces(await Workspace.get_by_user(user_id)),
            key=lambda item: _workspace_time(item[1]),
            reverse=True,
        )
        recent = [
            {
                "name": workspace.name or _workspace_display_name(path),
                "path": path,
            }
            for path, workspace in recents[:10]
        ]

    system_info["recent"] = recent
    return system_info


def _collect_system_info(user_home: Path) -> dict:
    """Gather all system info synchronously. Called via asyncio.to_thread()."""
    import platform
    import socket
    import shutil
    import subprocess
    import tempfile
    import time
    from importlib.metadata import version as pkg_version

    hostname = socket.gethostname()
    try:
        app_version = pkg_version("cptr")
    except Exception:
        app_version = "dev"

    # System stats
    system = {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count() or 0,
    }

    # Memory (cross-platform)
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            system["memory_total"] = int(result.stdout.strip())
            vm = subprocess.run(
                ["vm_stat"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            pages_free = 0
            page_size = 4096
            for line in vm.stdout.split("\n"):
                if "page size" in line:
                    page_size = int(line.split()[-2])
                if "Pages free" in line:
                    pages_free += int(line.split()[-1].rstrip("."))
                if "Pages inactive" in line:
                    pages_free += int(line.split()[-1].rstrip("."))
            system["memory_available"] = pages_free * page_size
        elif platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                mem = {}
                for line in f:
                    parts = line.split()
                    mem[parts[0].rstrip(":")] = int(parts[1]) * 1024
                system["memory_total"] = mem.get("MemTotal", 0)
                system["memory_available"] = mem.get("MemAvailable", 0)
        elif platform.system() == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            mem_info = MEMORYSTATUSEX()
            mem_info.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem_info))
            system["memory_total"] = mem_info.ullTotalPhys
            system["memory_available"] = mem_info.ullAvailPhys
    except Exception:
        pass

    # Disk
    try:
        disk = shutil.disk_usage(str(user_home))
        system["disk_total"] = disk.total
        system["disk_used"] = disk.used
        system["disk_free"] = disk.free
    except Exception:
        pass

    # Uptime
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            sec_str = result.stdout.split("sec =")[1].split(",")[0].strip()
            boot_time = int(sec_str)
            system["uptime_seconds"] = int(time.time() - boot_time)
        elif platform.system() == "Linux":
            with open("/proc/uptime") as f:
                system["uptime_seconds"] = int(float(f.read().split()[0]))
        elif platform.system() == "Windows":
            import ctypes

            ms = ctypes.windll.kernel32.GetTickCount64()
            system["uptime_seconds"] = ms // 1000
    except Exception:
        pass

    # Load average
    try:
        load = os.getloadavg()
        system["load_avg"] = [round(value, 2) for value in load]
    except Exception:
        pass

    # CPU usage
    try:
        if platform.system() == "Linux":
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()
            idle = int(parts[4])
            total = sum(int(p) for p in parts[1:])
            if total > 0:
                system["cpu_usage"] = round(100.0 * (1.0 - idle / total), 1)
        elif platform.system() == "Darwin":
            load = system.get("load_avg")
            cpus = system.get("cpu_count", 1)
            if load and cpus:
                system["cpu_usage"] = round(min(load[0] / cpus * 100, 100), 1)
        elif platform.system() == "Windows":
            result = subprocess.run(
                ["wmic", "cpu", "get", "loadpercentage", "/value"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if line.startswith("LoadPercentage="):
                    system["cpu_usage"] = float(line.split("=", 1)[1])
                    break
    except Exception:
        pass

    # Network interfaces
    try:
        interfaces = []
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["ifconfig"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            current_iface = ""
            for line in result.stdout.split("\n"):
                if line and not line.startswith("\t") and not line.startswith(" "):
                    current_iface = line.split(":")[0]
                if "inet " in line and "127.0.0.1" not in line:
                    ip = line.strip().split()[1]
                    interfaces.append({"name": current_iface, "ip": ip})
        elif platform.system() == "Linux":
            result = subprocess.run(
                ["ip", "-4", "-o", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 4 and "127.0.0.1" not in parts[3]:
                    interfaces.append({"name": parts[1], "ip": parts[3].split("/")[0]})
        elif platform.system() == "Windows":
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            current_iface = ""
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if line and not line.startswith(" ") and ":" in line:
                    current_iface = line.split(":")[0].strip()
                elif "IPv4" in stripped and ":" in stripped:
                    ip = stripped.rsplit(":", 1)[1].strip()
                    if ip != "127.0.0.1":
                        interfaces.append({"name": current_iface, "ip": ip})
        if interfaces:
            system["network"] = interfaces
    except Exception:
        pass

    # Top processes (by CPU). `args` gives the full command line (comm strips
    # the arguments), which is what identifies a runaway process as *this*
    # command rather than as "python".
    processes = []
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["ps", "-A", "-r", "-o", "pid,pcpu,pmem,args"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            processes = _parse_ps_processes(result.stdout, limit=5)
        elif platform.system() == "Linux":
            result = subprocess.run(
                ["ps", "-eo", "pid,pcpu,pmem,args", "--sort=-pcpu", "--no-headers"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            processes = _parse_ps_processes(result.stdout, limit=5, has_header=False)
        elif platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            import csv
            from io import StringIO

            reader = csv.reader(StringIO(result.stdout))
            mem_total = system.get("memory_total", 1)
            entries = []
            for row in reader:
                if len(row) >= 5:
                    try:
                        pid = int(row[1])
                        # Memory is in "N,NNN K" format
                        mem_str = row[4].replace(",", "").replace(" K", "").strip()
                        mem_kb = int(mem_str) if mem_str.isdigit() else 0
                        entries.append(
                            {
                                "pid": pid,
                                "cpu": 0,  # tasklist doesn't provide CPU%
                                "mem": round(mem_kb * 1024 / mem_total * 100, 1)
                                if mem_total
                                else 0,
                                "name": row[0],
                                "_mem_kb": mem_kb,
                            }
                        )
                    except (ValueError, IndexError):
                        pass
            # Sort by memory usage (best we can do without psutil)
            entries.sort(key=lambda e: e.get("_mem_kb", 0), reverse=True)
            commands = _windows_command_lines()
            for e in entries[:5]:
                e.pop("_mem_kb", None)
                command = commands.get(e["pid"])
                if command:
                    e["cmd"] = command[:400]
                processes.append(e)
    except Exception:
        pass

    # Suggested directories
    home_path = user_home
    home = str(home_path)
    candidates = [
        home,
        str(home_path / "Documents"),
        str(home_path / "Desktop"),
        str(home_path / "Downloads"),
        str(home_path / "Projects"),
        str(home_path / "projects"),
        str(home_path / "code"),
        str(home_path / "Code"),
        str(home_path / "dev"),
        str(home_path / "workspace"),
        str(home_path / "src"),
    ]
    if platform.system() == "Windows":
        candidates.extend(
            [
                str(home_path / "source" / "repos"),
                str(home_path / "OneDrive" / "Documents"),
            ]
        )
    candidates.append(tempfile.gettempdir())
    suggestions = []
    seen = set()
    for c in candidates:
        p = Path(c)
        if p.exists() and p.is_dir() and c not in seen:
            suggestions.append({"name": p.name or c, "path": c})
            seen.add(c)

    return {
        "hostname": hostname,
        "platform": platform.system(),
        "version": app_version,
        # The pid of this server: the UI needs it to tell "restarted" from
        # "still the old process" while it polls /api/health.
        "pid": os.getpid(),
        "system": system,
        "processes": processes,
        "suggestions": suggestions,
    }


# ── Processes and the server itself ──────────────────────────────
#
# The System info dialog reads processes from /welcome and drives two
# destructive actions from here: killing one pid, and restarting this server.
# Both are gated the same way the rest of the app is — any authenticated user,
# who can already open a terminal in this workspace — with the machine's own
# permissions as the real boundary (a process owned by another uid is refused).

MAX_COMMAND_CHARS = 400


class KillProcessBody(BaseModel):
    pid: int
    force: bool = False


class RestartServerBody(BaseModel):
    delay_seconds: float | None = None


def _process_name(command: str) -> str:
    """Short name for a process, given its full command line."""
    text = command.strip()
    if not text:
        return ""
    if text.startswith("["):
        # Kernel thread or zombie: ps prints "[kworker/u8:2]", "[git] <defunct>".
        return text.split()[0].strip("[]") or text
    if text[0] in "\"'":
        # A quoted executable can contain spaces, and Windows always quotes:
        # '"C:\Program Files\x\svchost.exe" -k netsvcs'.
        closing = text.find(text[0], 1)
        first = text[1:closing] if closing > 0 else text.strip("\"'")
    else:
        first = text.split()[0]
    return first.replace("\\", "/").rsplit("/", 1)[-1] or first


def _parse_ps_processes(stdout: str, limit: int, has_header: bool = True) -> list[dict]:
    """Rows of `ps -o pid,pcpu,pmem,args` → process dicts."""
    lines = [line for line in stdout.strip().split("\n") if line.strip()]
    if has_header and lines:
        lines = lines[1:]
    processes = []
    for line in lines[:limit]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            cpu = float(parts[1])
            mem = float(parts[2])
        except ValueError:
            continue
        command = parts[3].strip()[:MAX_COMMAND_CHARS]
        processes.append(
            {
                "pid": pid,
                "cpu": cpu,
                "mem": mem,
                "name": _process_name(command),
                "cmd": command,
            }
        )
    return processes


def _windows_command_lines() -> dict[int, str]:
    """Command lines for the running processes, via CIM (tasklist has none)."""
    import subprocess

    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,CommandLine | "
        "ConvertTo-Csv -NoTypeInformation"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    import csv
    from io import StringIO

    commands: dict[int, str] = {}
    for row in csv.reader(StringIO(result.stdout)):
        if len(row) < 2:
            continue
        pid_text, command = row[0].strip(), row[1].strip()
        if pid_text.isdigit() and command:
            commands[int(pid_text)] = command
    return commands


def _process_uid(pid: int) -> int | None:
    """Owning uid of a process, when the OS makes it cheap to ask (else None)."""
    import subprocess

    try:
        for line in Path(f"/proc/{pid}/status").read_text(errors="replace").splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "uid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _kill_process(pid: int, force: bool) -> dict:
    import signal
    import subprocess

    if pid <= 1:
        raise HTTPException(status_code=400, detail=f"refusing to signal pid {pid}")
    if pid == os.getpid():
        raise HTTPException(
            status_code=400, detail=f"pid {pid} is this cptr server — restart it instead"
        )
    if os.name == "posix" and hasattr(os, "getuid"):
        owner = _process_uid(pid)
        if owner is not None and owner != os.getuid() and os.getuid() != 0:
            raise HTTPException(
                status_code=403, detail=f"process {pid} is owned by uid {owner}"
            )
    sent = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sent)
    except ProcessLookupError:
        raise HTTPException(status_code=404, detail=f"no process with pid {pid}") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"not permitted to signal pid {pid}") from None
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"could not signal pid {pid}: {exc}") from exc
    return {"status": "signalled", "pid": pid, "signal": sent.name, "force": bool(force)}


@router.post("/processes/kill")
async def kill_process(request: Request, body: KillProcessBody):
    """Signal one process, for the ✕ beside a row in System info."""
    import asyncio

    if not await _get_user_id(request):
        raise HTTPException(status_code=403, detail="sign in required")
    return await asyncio.to_thread(_kill_process, body.pid, body.force)


# A restart takes seconds; a second one inside that window would race the first
# worker for the port and leave two servers fighting over it.
RESTART_GUARD_SECONDS = 60.0
_restart_requested_at = 0.0


@router.post("/server/restart")
async def restart_server(request: Request, body: RestartServerBody | None = None):
    """Stop this server and start it again — same command line, same port."""
    import asyncio
    import time

    global _restart_requested_at

    if not await _get_user_id(request):
        raise HTTPException(status_code=403, detail="sign in required")

    now = time.time()
    if _restart_requested_at and now - _restart_requested_at < RESTART_GUARD_SECONDS:
        raise HTTPException(status_code=409, detail="a restart is already in progress")
    _restart_requested_at = now

    from cptr.utils.restart import DEFAULT_DELAY, MAX_DELAY, MIN_DELAY, spawn_restart

    requested = body.delay_seconds if body else None
    delay = DEFAULT_DELAY if requested is None else float(requested)
    delay = max(MIN_DELAY, min(delay, MAX_DELAY))

    auth = getattr(request.state, "auth", None)
    who = getattr(auth, "username", None) or "unknown"
    try:
        info = await asyncio.to_thread(spawn_restart, delay=delay, reason=f"api:{who}")
    except Exception as exc:  # nothing to restart with: say so and clear the guard
        _restart_requested_at = 0.0
        raise HTTPException(status_code=500, detail=f"could not schedule the restart: {exc}") from exc

    return {
        "status": "restarting",
        "pid": info["pid"],
        "delay_seconds": info["delay"],
        "port": info["port"],
        "log": info["log"],
    }
