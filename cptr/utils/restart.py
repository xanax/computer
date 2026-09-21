"""Restart the running cptr server from inside it.

`cptr run` is its own supervisor — there is no watchdog to bring the process
back — so the restart has to be handed to something that outlives the process
being restarted::

    POST /api/state/server/restart
      └─ spawn_restart()                        inside the old server
           └─ python -m cptr.utils.restart '<json>'   detached, own session
                ├─ sleep (let the HTTP response and the last UI paint leave)
                ├─ SIGTERM the old server, SIGKILL if it will not go
                ├─ wait for the port to be released
                ├─ start the same command line again, detached, logging to a file
                └─ poll /api/health until the new server answers

Two details that are easy to get wrong:

* the worker must be a separate **process**, not a thread: an orderly SIGTERM
  makes uvicorn shut the event loop down, so anything still pending inside the
  old server cannot be trusted to finish;
* it must be in its own **session** (`start_new_session`): the server parents
  every agent shell and every detached job, so a signal aimed at the process
  group would take the worker out too, and the machine would be left with no
  server and no one to start one.

The command line is rebuilt from `sys.argv`, so whatever launched this server —
`cptr run --port 4200 --headless`, an `-m cptr.cli` invocation, a lane script —
is what comes back. Console output of the new server goes to the same place the
first launch wrote it (`cptr-start.log` beside the server, else the data dir).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DELAY = 3.0  # seconds between the HTTP response and the kill
DEFAULT_GRACE = 30.0  # seconds to wait for the old process to release the port
MIN_DELAY = 0.5
MAX_DELAY = 60.0
HEALTH_TIMEOUT = 60.0


# ── Describing the current process ───────────────────────────────


def _module_invocation(script: str) -> list[str] | None:
    """`python -m pkg.mod` for a script that lives inside an importable package.

    `python -m cptr.cli` reports `sys.argv[0]` as `/…/cptr/cli.py`, so the
    module path has to be walked back from the file. Returns None when the file
    is not part of a package (a plain script, or a console script entry point).
    """
    path = Path(script)
    if path.suffix != ".py":
        return None
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    parts = [path.stem]
    parent = path.parent
    while (parent / "__init__.py").is_file():
        parts.insert(0, parent.name)
        parent = parent.parent
    if len(parts) < 2:
        return None
    return [sys.executable, "-m", ".".join(parts)]


def launcher_root() -> Path:
    """Directory that has to be on `sys.path` for `-m cptr.cli` to work."""
    return Path(__file__).resolve().parents[2]


def server_command(argv: list[str] | None = None) -> list[str]:
    """The command line that recreates the current process."""
    argv = list(sys.argv if argv is None else argv)
    if not argv:
        return [sys.executable, "-m", "cptr.cli", "run"]

    first = argv[0]
    module = _module_invocation(first) if first else None
    if module:
        return [*module, *argv[1:]]
    if first and _is_file(first):
        # A console script (`.venv/bin/cptr`) or an explicit script path: both
        # are Python files the same interpreter can run.
        return [sys.executable, os.path.abspath(first), *argv[1:]]
    # A bare name resolved through PATH (`cptr run`): the entry point is cptr.cli.
    return [sys.executable, "-m", "cptr.cli", *argv[1:]]


def _is_file(path: str) -> bool:
    try:
        return Path(path).is_file()
    except OSError:
        return False


def port_from_argv(argv: list[str] | None = None) -> int | None:
    """The port this server was told to bind, from its own arguments."""
    argv = list(sys.argv if argv is None else argv)
    for index, token in enumerate(argv):
        value: str | None = None
        if token == "--port" and index + 1 < len(argv):
            value = argv[index + 1]
        elif token.startswith("--port="):
            value = token.split("=", 1)[1]
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            continue
    env_port = os.environ.get("CPTR_PORT", "")
    return int(env_port) if env_port.isdigit() else None


def log_path(cwd: Path) -> Path:
    """Where the restarted server should write its output."""
    first_launch = Path(cwd) / "cptr-start.log"
    if first_launch.exists():
        return first_launch
    from cptr.env import DATA_DIR

    return DATA_DIR / "server.log"


# ── Spawning the worker (runs inside the old server) ─────────────


def spawn_restart(
    *,
    delay: float | None = None,
    grace: float | None = None,
    port: int | None = None,
    reason: str = "api",
) -> dict:
    """Hand the restart to a detached worker; return what was scheduled."""
    cwd = Path.cwd()
    payload = {
        "pid": os.getpid(),
        "cmd": server_command(),
        "cwd": str(cwd),
        "log": str(log_path(cwd)),
        "delay": DEFAULT_DELAY if delay is None else float(delay),
        "grace": DEFAULT_GRACE if grace is None else float(grace),
        "port": port_from_argv() if port is None else port,
        "reason": reason,
    }
    env = os.environ.copy()
    root = str(launcher_root())
    python_path = [root]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)

    worker = [sys.executable, "-m", "cptr.utils.restart", json.dumps(payload)]
    with open(os.devnull, "rb") as devnull:
        subprocess.Popen(  # noqa: S603 - argv is ours, built from sys.argv
            worker,
            cwd=str(cwd),
            env=env,
            stdin=devnull,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
            close_fds=True,
        )
    return payload


# ── The worker ───────────────────────────────────────────────────


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _logger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        line = f"[restart {_stamp()}] {message}\n"
        try:
            with open(path, "a", encoding="utf-8", errors="replace") as handle:
                handle.write(line)
        except OSError:
            pass

    return log


def _alive(pid: int) -> bool:
    """True while the pid exists. A zombie does not count as alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(errors="replace")
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _port_open(port: int | None, host: str = "127.0.0.1") -> bool:
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _signal(pid: int, sig: int, log) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except OSError as exc:
        log(f"could not send {sig.name} to pid {pid}: {exc}")
        return False


def _await_gone(pid: int, port: int | None, timeout: float) -> bool:
    """Wait until the old server is gone, or has at least let the port go."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid) or (port is not None and not _port_open(port)):
            return True
        time.sleep(0.25)
    return not _alive(pid) or (port is not None and not _port_open(port))


def _stop(pid: int, port: int | None, grace: float, log) -> None:
    """Stop the old server; escalate once if it does not go quietly."""
    if _signal(pid, signal.SIGTERM, log):
        log(f"SIGTERM → pid {pid}")
    else:
        log(f"pid {pid} was already gone")

    half = max(grace / 2, 1.0)
    if _await_gone(pid, port, half):
        return

    log(f"pid {pid} still holds :{port} after {half:g}s — SIGKILL")
    _signal(pid, signal.SIGKILL, log)
    if not _await_gone(pid, port, max(grace - half, 5.0)):
        log(f"warning: :{port} is still in use; starting anyway")


def _start(payload: dict, log) -> int | None:
    """Start the server again, detached, appending to the same log."""
    cmd = payload["cmd"]
    log_file = payload["log"]
    try:
        handle = open(log_file, "a", encoding="utf-8", errors="replace")
    except OSError as exc:
        log(f"cannot open {log_file}: {exc}")
        return None
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv comes from our own payload
            cmd,
            cwd=payload["cwd"],
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        log(f"failed to start the server: {exc}")
        return None
    finally:
        handle.close()
    log(f"started pid {proc.pid}: {' '.join(cmd)}")
    return proc.pid


def _await_health(port: int | None, log) -> bool:
    """Give the new server a chance to say it is up before the worker exits."""
    if not port:
        return False
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    log(f"health 200 at {url}")
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(1.0)
    log(f"warning: no health response from {url} within {HEALTH_TIMEOUT:g}s")
    return False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m cptr.utils.restart '<json payload>'", file=sys.stderr)
        return 2
    try:
        payload = json.loads(argv[0])
    except ValueError as exc:
        print(f"bad payload: {exc}", file=sys.stderr)
        return 2

    log = _logger(Path(payload["log"]))
    log(f"=== restart requested ({payload.get('reason', 'unknown')}) ===")
    log(f"command: {' '.join(payload['cmd'])} (cwd {payload['cwd']})")

    delay = float(payload.get("delay") or 0)
    if delay > 0:
        log(f"waiting {delay:g}s for the response to leave the old server")
        time.sleep(delay)

    pid = int(payload["pid"])
    port = payload.get("port")
    grace = max(float(payload.get("grace") or DEFAULT_GRACE), 1.0)
    _stop(pid, port, grace, log)

    if _start(payload, log) is not None:
        _await_health(port, log)
    log("=== restart finished ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
