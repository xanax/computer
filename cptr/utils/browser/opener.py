"""Hand a URL to the browser the *user* is looking at.

The ``browser_*`` tools drive a headless Chrome over CDP: fast, scriptable and
completely invisible to the human. This module is the other end of the
spectrum -- it puts a page in front of the person, so a chat that just built or
started a web app can show it without being asked twice.

Platform notes worth remembering:

* **WSL** -- the server is Linux, but the desktop and the installed browsers
  live on the Windows side. ``xdg-open`` has no handler there, so we go through
  Windows interop (``explorer.exe``, falling back to PowerShell's
  ``Start-Process``). ``file://`` paths need translating too: ``/home/...`` does
  not exist for Windows, but ``\\\\wsl.localhost\\<distro>\\home\\...`` does.
* **macOS** -- ``open``.
* **Windows** -- ``os.startfile``.
* **Linux** -- ``xdg-open``.

Launches are detached and fire-and-forget: a browser that never shows up must
never wedge the agent loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_PROC_VERSION = Path("/proc/version")

# WSL interop executables. ``explorer.exe`` is the least fussy way to hand a URL
# to the Windows shell -- no command line parsing, so query strings survive.
_EXPLORER_CANDIDATES = (
    "/mnt/c/Windows/explorer.exe",
)

_POWERSHELL_CANDIDATES = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/Program Files/PowerShell/7/pwsh.exe",
)

_MACOS_OPEN_CANDIDATES = ("/usr/bin/open",)

# Keep strong references to the reaper tasks; a bare create_task() can be
# garbage collected before it ever runs.
_reapers: set[asyncio.Task] = set()


def is_wsl() -> bool:
    """True when we are Linux under WSL (i.e. Windows owns the desktop)."""
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in _PROC_VERSION.read_text(errors="replace").lower()
    except OSError:
        return False


def platform_name() -> str:
    """One of ``windows``, ``macos``, ``wsl`` or ``linux``."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "wsl" if is_wsl() else "linux"


def _first_existing(candidates: tuple[str, ...], fallback: str = "") -> str:
    for path in candidates:
        if Path(path).exists():
            return path
    return shutil.which(fallback) if fallback else ""


def _windows_path(path: Path) -> str:
    """Spell a WSL path the way Windows understands it."""
    parts = path.parts
    if len(parts) > 2 and parts[1] == "mnt" and len(parts[2]) == 1:
        rest = "\\".join(parts[3:])
        return f"{parts[2].upper()}:\\{rest}" if rest else f"{parts[2].upper()}:\\"
    distro = os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"
    rest = "\\".join(parts[1:])
    return f"\\\\wsl.localhost\\{distro}\\{rest}" if rest else f"\\\\wsl.localhost\\{distro}"


def windows_target(url: str) -> str:
    """Translate a URL for the Windows shell (``file://`` becomes a path)."""
    if not url.lower().startswith("file://"):
        return url
    raw = url[len("file://") :]
    if raw.lower().startswith("localhost/"):
        raw = raw[len("localhost") :]
    return _windows_path(Path(unquote(raw)))


def _powershell_quote(value: str) -> str:
    """Quote for a PowerShell single-quoted string literal."""
    return value.replace("'", "''")


def launch_commands(url: str) -> list[list[str]]:
    """Ordered candidate command lines that open ``url``, best first."""
    kind = platform_name()

    if kind == "macos":
        return [[_first_existing(_MACOS_OPEN_CANDIDATES, "open") or "open", url]]

    if kind == "wsl":
        target = windows_target(url)
        commands: list[list[str]] = []
        explorer = _first_existing(_EXPLORER_CANDIDATES, "explorer.exe")
        if explorer:
            commands.append([explorer, target])
        powershell = _first_existing(_POWERSHELL_CANDIDATES, "powershell.exe")
        if powershell:
            commands.append(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"Start-Process -FilePath '{_powershell_quote(target)}'",
                ]
            )
        if xdg_open := shutil.which("xdg-open"):
            commands.append([xdg_open, url])
        return commands

    return [[shutil.which("xdg-open") or "xdg-open", url]]


async def _reap(proc: asyncio.subprocess.Process, name: str) -> None:
    """Wait for a detached launcher so it does not linger as a zombie."""
    try:
        code = await asyncio.wait_for(proc.wait(), timeout=30)
    except asyncio.TimeoutError:
        logger.debug("[opener] %s still running; leaving it detached", name)
        return
    # explorer.exe exits 1 even when it successfully handed the URL over, so
    # exit codes here are informational only.
    logger.debug("[opener] %s exited %s", name, code)


async def _spawn_detached(argv: list[str]) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError) as exc:
        return False, f"{Path(argv[0]).name}: {exc}"

    task = asyncio.create_task(_reap(proc, Path(argv[0]).name))
    _reapers.add(task)
    task.add_done_callback(_reapers.discard)
    return True, Path(argv[0]).name


async def open_in_system_browser(url: str) -> tuple[bool, str]:
    """Open ``url`` in the desktop's default browser.

    Returns ``(launched, detail)``. ``launched`` means the launch command was
    spawned cleanly -- the desktop may still take a moment to paint the window,
    and there is no portable way to confirm that it did.
    """
    if not url:
        return False, "no URL given"

    if platform_name() == "windows":
        try:
            os.startfile(url)  # type: ignore[attr-defined]  # Windows only
        except OSError as exc:
            return False, f"Windows shell: {exc}"
        return True, "Windows shell"

    problems: list[str] = []
    for argv in launch_commands(url):
        launched, detail = await _spawn_detached(argv)
        if launched:
            return True, detail
        problems.append(detail)
    return False, "; ".join(problems) or "no way to open a browser was found"
