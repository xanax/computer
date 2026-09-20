"""Tool definitions: plain functions with schema introspection.

Tools are real async functions. Schemas are auto-generated from
type hints + docstrings via inspect. Keyword-only parameters are
never exposed to the LLM — they carry injected execution context:

  - Legacy tools use ``*, workspace: str`` (injected by execute_tool).
  - Context-aware tools use ``*, __context__: dict`` containing
    workspace, user_id, and model_id (injected by execute_tool).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import mimetypes
import os
import re
import stat
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Literal, Optional, get_args, get_origin, get_type_hints
from urllib.parse import urlencode, urlsplit

from fastapi import Request
from cptr.env import CHAT_TOOL_COMMAND_MAX_CHARS, CHAT_TOOL_MAX_CHARS, EXECUTE_TIMEOUT
from cptr.utils import git as gitlib
from cptr.utils.gitignore import is_gitignored, load_gitignore
from cptr.utils.identity import (
    IdentityUnavailable,
    env_for,
    expand_user_path,
    identity_for_context,
    preexec_for,
)
from cptr.utils.runtime import Runtime, FileError

try:
    import fcntl
    import pty
    import signal
    import struct
    import subprocess
    import termios

    _PTY_AVAILABLE = True
except ImportError:
    import signal
    import subprocess

    _PTY_AVAILABLE = False  # Windows


# ── Command session state ───────────────────────────────────

command_sessions: dict[str, dict] = {}
# command_session_id → {
#   "master_fd": int | None,   PTY mode (Unix) — read/write through this fd
#   "proc": Popen | Process,   The child process handle
#   "output": bytearray,       In-memory ring buffer (256KB cap)
#   "command": str,
#   "done": bool,
#   "exit_code": int | None,
#   "log_path": str,
# }
MAX_COMMAND_SESSIONS = 5
_MAX_LOG_SIZE = 50 * 1024 * 1024  # 50MB — rotate when exceeded

VALID_TASK_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
MAX_TASK_ITEMS = 256
MAX_TASK_CONTENT_CHARS = 4000
_TASK_TRUNCATION_MARKER = "... [truncated]"


def _spawn_pty(command: str, cwd: str, env: dict, preexec_fn=None) -> tuple:
    """Spawn a command under a PTY (Unix only). Returns (proc, master_fd)."""
    master_fd, slave_fd = pty.openpty()
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        proc = subprocess.Popen(
            command,
            shell=True,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            start_new_session=True,
            preexec_fn=preexec_fn,
        )
    except Exception:
        os.close(slave_fd)
        os.close(master_fd)
        raise
    os.close(slave_fd)
    return proc, master_fd


def _kill_process_group(pid: int, force: bool = False) -> None:
    """Send signal to the child's entire process group.

    SIGTERM for graceful shutdown (default), SIGKILL for force.
    Falls back to signalling just the leader if the group is gone.
    """
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def _rotate_log(log_path: str, log_file) -> tuple:
    """Keep the newest half of the log file. Returns new (file, bytes_written)."""
    log_file.flush()
    log_file.close()

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    keep = lines[len(lines) // 2 :]

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "log_rotated", "ts": time.time()}) + "\n")
        for line in keep:
            f.write(line)

    new_file = open(log_path, "a", encoding="utf-8")
    new_size = sum(len(line.encode("utf-8", errors="replace")) for line in keep)
    return new_file, new_size


async def stream_command_session_output(command_session_id: str):
    """Read output from a command process into memory + JSONL log."""
    session = command_sessions.get(command_session_id)
    if not session:
        return

    master_fd = session.get("master_fd")
    proc = session["proc"]
    log_path = session.get("log_path")
    log_file = None
    log_bytes = 0
    loop = asyncio.get_event_loop()

    try:
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "a", encoding="utf-8")
            entry = (
                json.dumps(
                    {
                        "type": "start",
                        "command": session["command"],
                        "pid": proc.pid,
                        "ts": time.time(),
                    }
                )
                + "\n"
            )
            log_file.write(entry)
            log_file.flush()
            log_bytes += len(entry.encode("utf-8", errors="replace"))

        while True:
            # Read from PTY fd (Unix) or subprocess pipe (Windows fallback)
            if master_fd is not None:
                try:
                    chunk = await loop.run_in_executor(None, os.read, master_fd, 4096)
                    if not chunk:
                        break
                except OSError:
                    break  # EIO when child exits
            else:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break

            session = command_sessions.get(command_session_id)
            if session:
                session["output"].extend(chunk)
                session["total_bytes"] += len(chunk)
                if len(session["output"]) > 256 * 1024:
                    session["output"] = session["output"][-256 * 1024 :]
                async with session["condition"]:
                    session["condition"].notify_all()

            if log_file:
                entry = (
                    json.dumps(
                        {
                            "type": "output",
                            "data": chunk.decode(errors="replace"),
                            "ts": time.time(),
                        }
                    )
                    + "\n"
                )
                entry_size = len(entry.encode("utf-8", errors="replace"))
                if log_bytes + entry_size > _MAX_LOG_SIZE:
                    log_file, log_bytes = _rotate_log(log_path, log_file)
                log_file.write(entry)
                log_file.flush()
                log_bytes += entry_size
    except Exception:
        pass
    finally:
        # Wait for the process to finish and collect exit code
        session = command_sessions.get(command_session_id)
        if master_fd is not None:
            exit_code = await loop.run_in_executor(None, proc.wait)
            try:
                os.close(master_fd)
            except OSError:
                pass
        else:
            await proc.wait()
            exit_code = proc.returncode

        if session:
            session["done"] = True
            session["exit_code"] = exit_code
            session["master_fd"] = None  # fd is closed
            async with session["condition"]:
                session["condition"].notify_all()

        if log_file:
            log_file.write(
                json.dumps({"type": "end", "exit_code": exit_code, "ts": time.time()}) + "\n"
            )
            log_file.close()


# ── Helper ──────────────────────────────────────────────────


def _is_dotenv(path: Path) -> bool:
    """Return True if the path refers to a .env file (e.g. .env, .env.local)."""
    return path.name == ".env" or path.name == ".envrc" or path.name.startswith(".env.")


_DOTENV_ERROR = "Error: access to .env files is not allowed for security reasons."


def _safe_download_name(name: str) -> str:
    """Sanitize a caller-supplied download filename (no paths, no header breakers)."""
    cleaned = "".join(c for c in (name or "").strip() if c.isprintable() and c not in '\\/"')
    cleaned = cleaned.strip(". ")
    return cleaned if cleaned not in {"", ".", ".."} else ""

_SENSITIVE_READ_ERROR = "Error: access to credential files is not allowed for security reasons."

_SENSITIVE_HOME_FILES = {
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pgpass",
    ".pypirc",
}

_SENSITIVE_HOME_DIRS = {
    ".aws",
    ".config/gh",
    ".config/gcloud",
    ".config/op",
    ".docker",
    ".gnupg",
    ".kube",
    ".ssh",
}


def _human_size(size: int) -> str:
    """Format byte size for display."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _file_kind(path: Path, mime_type: str) -> str:
    ext = path.suffix.lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type == "application/pdf":
        return "pdf"
    if ext in {".md", ".markdown", ".mdx"}:
        return "markdown"
    if ext in {".json", ".jsonc", ".json5"}:
        return "json"
    if ext in {".csv", ".tsv"}:
        return "csv"
    if ext in {".html", ".htm"}:
        return "html"
    if ext == ".svg":
        return "svg"
    if ext in {".txt", ".log", ".diff", ".patch"} or mime_type.startswith("text/"):
        return "text"
    if ext in {".docx", ".xlsx", ".xls", ".pptx"}:
        return "office"
    if ext in {".sqlite", ".sqlite3", ".db", ".db3"}:
        return "sqlite"
    if ext in {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"}:
        return "archive"
    return "binary"


def _truncate_output(text: str, max_chars: int = 80_000) -> str:
    """Truncate long output, keeping head and tail."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n... (truncated) ...\n\n" + text[-half:]


def _command_session_snapshot(command_session_id: str, session: dict) -> dict[str, Any]:
    return {
        "command_session_id": command_session_id,
        "task_id": command_session_id,  # legacy UI/tool wording
        "workspace": session.get("workspace", ""),
        "chat_id": session.get("chat_id"),
        "message_id": session.get("message_id"),
        "call_id": session.get("call_id"),
        "command": session.get("command", ""),
        "created_at": session.get("created_at", 0),
        "status": "completed" if session.get("done") else "running",
        "done": bool(session.get("done")),
        "exit_code": session.get("exit_code"),
        "total_bytes": int(session.get("total_bytes") or 0),
        "output": bytes(session.get("output") or b"").decode(errors="replace"),
    }


def list_command_sessions(
    request,
    workspace: str | None = None,
    chat_id: str | None = None,
    auth=None,
    context: dict | None = None,
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    if context and context.get("request") is not None:
        request = context["request"]
    if request is not None:
        auth = getattr(getattr(request, "state", None), "auth", None)
    user_id = getattr(auth, "user_id", None) if auth is not None else None
    if user_id is None and context:
        user_id = context.get("user_id")
    for command_session_id, session in command_sessions.items():
        if session.get("done"):
            continue
        if workspace and session.get("workspace") != workspace:
            continue
        if chat_id and session.get("chat_id") != chat_id:
            continue
        if user_id is not None and session.get("user_id") != user_id:
            continue
        sessions.append(_command_session_snapshot(command_session_id, session))
    sessions.sort(key=lambda item: (item["status"] != "running", -float(item["created_at"] or 0)))
    return sessions


def get_command_session(
    request,
    command_session_id: str = "",
    auth=None,
    context: dict | None = None,
) -> dict | None:
    session = command_sessions.get(command_session_id)
    if context and context.get("request") is not None:
        request = context["request"]
    if request is not None:
        auth = getattr(getattr(request, "state", None), "auth", None)
    user_id = getattr(auth, "user_id", None) if auth is not None else None
    if user_id is None and context:
        user_id = context.get("user_id")
    if not session or (user_id is not None and session.get("user_id") != user_id):
        return None
    return session


def command_session_bytes_since(session: dict, offset: int) -> tuple[bytes, int]:
    buf = session["output"]
    total = int(session.get("total_bytes") or 0)
    buf_start = total - len(buf)
    if offset <= buf_start:
        raw = bytes(buf)
    else:
        raw = bytes(buf[offset - buf_start :])
    return raw, total


def send_command_session_input(
    request, command_session_id: str, data: bytes, **scope
) -> str | None:
    session = get_command_session(request, command_session_id, **scope)
    if not session:
        return "command session not found"
    if session.get("done"):
        return "command session already exited"

    master_fd = session.get("master_fd")
    if master_fd is not None:
        try:
            os.write(master_fd, data)
        except OSError:
            return "PTY closed"
    else:
        proc = session["proc"]
        if proc.stdin is None:
            return "stdin unavailable"
        try:
            proc.stdin.write(data)
            if hasattr(proc.stdin, "drain"):
                # asyncio subprocess pipe
                return None
        except (BrokenPipeError, ConnectionResetError, OSError):
            return "stdin closed"
    return None


async def drain_command_session_input(request, command_session_id: str, **scope) -> None:
    session = get_command_session(request, command_session_id, **scope)
    proc = session.get("proc") if session else None
    stdin = getattr(proc, "stdin", None)
    if stdin is not None and hasattr(stdin, "drain"):
        await stdin.drain()


def resize_command_session(request, command_session_id: str, rows: int, cols: int, **scope) -> None:
    session = get_command_session(request, command_session_id, **scope)
    if not session or session.get("done"):
        return
    master_fd = session.get("master_fd")
    if master_fd is None or not _PTY_AVAILABLE:
        return
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except OSError:
        pass


def stop_command_session(
    request, command_session_id: str, force: bool = False, **scope
) -> str | None:
    session = get_command_session(request, command_session_id, **scope)
    if not session:
        return "command session not found"
    if session.get("done"):
        return None
    _kill_process_group(session["proc"].pid, force=force)
    return None


# ── Image support ───────────────────────────────────────────

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
}

_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB target for API payload

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def _read_image_file(full: Path, path: str) -> str:
    """Read an image file and return a data URI string.

    If the file exceeds _IMAGE_MAX_BYTES, attempts to resize it down
    using Pillow.  Falls back to a text error if Pillow is unavailable
    and the file is too large.
    """
    return _read_image_data(full.read_bytes(), full.suffix, path)


def _read_image_data(data: bytes, suffix: str, path: str) -> str:
    import base64

    size = len(data)
    ext = suffix.lower()
    media_type = _IMAGE_MIME.get(ext, "image/png")

    if size > _IMAGE_MAX_BYTES:
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(data))
            # Progressively scale down until under limit
            # Use JPEG for lossy formats, PNG for lossless
            out_format = "JPEG" if ext in (".jpg", ".jpeg", ".bmp", ".tiff", ".tif") else "PNG"
            if out_format == "JPEG":
                media_type = "image/jpeg"
                # Convert RGBA to RGB for JPEG
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
            else:
                media_type = "image/png"

            scale = 0.8  # start at 80%
            for _ in range(10):
                new_w = int(img.width * scale)
                new_h = int(img.height * scale)
                if new_w < 100 or new_h < 100:
                    break
                resized = img.resize((new_w, new_h), Image.LANCZOS)
                buf = io.BytesIO()
                save_kwargs = {"quality": 85} if out_format == "JPEG" else {}
                resized.save(buf, format=out_format, **save_kwargs)
                if buf.tell() <= _IMAGE_MAX_BYTES:
                    data = buf.getvalue()
                    size = len(data)
                    break
                scale *= 0.7  # more aggressive on each pass
            else:
                return f"Error: image too large ({_human_size(size)}) and could not be resized below 5MB."
        except ImportError:
            return (
                f"Error: image file is too large ({_human_size(size)}). "
                f"Install Pillow (`pip install Pillow`) to enable automatic resizing."
            )

    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{b64}"


# ── Tool functions ──────────────────────────────────────────


async def read_file(
    path: str,
    start_line: int = 0,
    end_line: int = 0,
    *,
    __context__: dict,
) -> str:
    """Read file contents with optional line range. Lines are 1-indexed.
    Supports relative workspace paths, absolute paths, and ~/ paths.
    Blocks .env, common credential files, and special device/socket files.
    :param path: Path relative to workspace root, absolute path, or ~/ path.
    :param start_line: First line to read (1-indexed, 0 = from beginning).
    :param end_line: Last line to read (inclusive, 0 = to end of file).
    """
    workspace = __context__["workspace"]
    try:
        identity = await identity_for_context(__context__)
    except IdentityUnavailable as e:
        return f"Error: {e}"

    p = expand_user_path(path, identity)
    if p.is_absolute():
        full = p.resolve()
    else:
        ws = Path(workspace).resolve()
        full = (ws / path).resolve()
        if not full.is_relative_to(ws):
            raise ValueError(f"Path traversal rejected: {path}")

    if _is_dotenv(full):
        return _DOTENV_ERROR

    resolved = full.resolve()
    home = Path(identity.home).resolve()
    home_rel = resolved.relative_to(home).as_posix() if resolved.is_relative_to(home) else ""
    if home_rel in _SENSITIVE_HOME_FILES or any(
        home_rel == d or home_rel.startswith(f"{d}/") for d in _SENSITIVE_HOME_DIRS
    ):
        return _SENSITIVE_READ_ERROR

    request = __context__.get("request")
    try:
        if request is None:
            return "Error: request context unavailable"
        file_stat = await Runtime.stat(request, str(full))
    except FileError:
        return f"Error: file not found: {path}"

    mode = int(file_stat.get("mode") or 0)
    if any(check(mode) for check in (stat.S_ISBLK, stat.S_ISCHR, stat.S_ISFIFO, stat.S_ISSOCK)):
        return f"Error: cannot read special file: {full}"

    if file_stat.get("type") != "file":
        return f"Error: file not found: {path}"

    # Image files: return base64 JSON instead of garbled text
    if full.suffix.lower() in IMAGE_EXTENSIONS:
        try:
            image = await Runtime.read_bytes(request, str(full))
        except FileError as e:
            return f"Error: {e}"
        return await asyncio.to_thread(_read_image_data, image["data"], full.suffix, path)

    size = int(file_stat.get("size") or 0)
    if size > 500_000:
        return f"Error: file too large ({size} bytes, max 500KB)"

    try:
        file_data = await Runtime.read_file(request, str(full))
    except FileError as e:
        return f"Error: {e}"
    if file_data.get("binary"):
        try:
            extracted = await Runtime.extract_text(request, str(full))
        except FileError as e:
            return f"Error: {e}"
        content = str(extracted.get("text") or "")
        if not content:
            return f"Error: binary file ({full.suffix}), cannot read as text"
    else:
        content = str(file_data.get("content") or "")

    def _format_text():
        lines = content.splitlines()
        total = len(lines)

        if start_line > 0 or end_line > 0:
            s = max(1, start_line) - 1  # Convert to 0-indexed
            e = min(total, end_line) if end_line > 0 else total
            selected = lines[s:e]
            numbered = [f"{i + s + 1}: {line}" for i, line in enumerate(selected)]
            header = f"File: {path} | Lines {s + 1}-{e} of {total}\n"
            return header + "\n".join(numbered)
        else:
            # Cap at 800 lines, show line numbers
            capped = lines[:800]
            numbered = [f"{i + 1}: {line}" for i, line in enumerate(capped)]
            header = f"File: {path} | Total lines: {total}"
            if total > 800:
                header += " (showing first 800)"
            return header + "\n" + "\n".join(numbered)

    return await asyncio.to_thread(_format_text)


async def list_directory(
    path: str = ".",
    recursive: bool = False,
    *,
    __context__: dict,
) -> str:
    """List files and directories with metadata (sizes, child counts).
    :param path: Directory path relative to workspace root.
    :param recursive: Whether to list recursively.
    """
    workspace = __context__["workspace"]
    request = __context__.get("request")
    try:
        full = _resolve_path(path, workspace)
        if request is None:
            return "Error: request context unavailable"
        result = await Runtime.list_tree(request, str(full), recursive)
    except (ValueError, FileError, IdentityUnavailable) as e:
        return f"Error: {e}"
    res = str(result.get("text") or "")
    return _truncate_output(res, max_chars=CHAT_TOOL_MAX_CHARS)


async def search_files(
    query: str,
    path: str = ".",
    regex: bool = False,
    case_insensitive: bool = False,
    include: str = "",
    exclude: str = "",
    type: str = "",
    context: int = 0,
    max_results: int = 50,
    word: bool = False,
    multiline: bool = False,
    filenames_only: bool = False,
    *,
    __context__: dict,
) -> str:
    """Search files for a pattern using ripgrep. Fast, respects .gitignore.
    :param query: Search pattern (plain text or regex).
    :param path: Directory to search in, relative to workspace.
    :param regex: Treat query as a regular expression.
    :param case_insensitive: Case-insensitive matching.
    :param include: Comma-separated globs to include (e.g. '*.py', '*.ts').
    :param exclude: Comma-separated globs to exclude (e.g. '*.min.js', 'dist/*').
    :param type: Comma-separated ripgrep file types (e.g. 'py', 'rust', 'markdown').
    :param context: Number of context lines to show around each match.
    :param max_results: Maximum matching lines to return (1-500).
    :param word: Match whole words only.
    :param multiline: Enable multiline matching.
    :param filenames_only: Only return filenames, not matching lines.
    """
    workspace = __context__["workspace"]
    request = __context__.get("request")
    try:
        full = _resolve_path(path, workspace)
        if request is None:
            return "Error: request context unavailable"
        directory = await Runtime.stat(request, str(full))
    except (ValueError, FileError, IdentityUnavailable) as e:
        return f"Error: {e}"
    if directory.get("type") != "directory":
        return f"Error: not a directory: {path}"

    # Try ripgrep first
    try:
        res = await _search_rg(
            query,
            full,
            regex=regex,
            case_insensitive=case_insensitive,
            include=include,
            exclude=exclude,
            type=type,
            context=context,
            max_results=max_results,
            word=word,
            multiline=multiline,
            filenames_only=filenames_only,
            identity=await identity_for_context(__context__),
        )
    except FileNotFoundError:
        try:
            matches = await Runtime.file_matches(request, query, str(full), False, 0, 50)
        except FileError as e:
            return f"Error: {e}"
        rows = []
        for item in matches.get("results", []):
            rel = item.get("relative_path") or item.get("name") or ""
            if filenames_only or item.get("name_match"):
                rows.append(str(rel))
            else:
                for match in item.get("content_matches") or []:
                    rows.append(f"{rel}:{match.get('line')}: {match.get('text')}")
        res = "\n".join(rows) if rows else "No matches found."

    return _truncate_output(res, max_chars=CHAT_TOOL_MAX_CHARS)


async def _search_rg(
    query: str,
    full: Path,
    regex: bool,
    case_insensitive: bool,
    include: str,
    exclude: str,
    type: str,
    context: int,
    max_results: int,
    word: bool,
    multiline: bool,
    filenames_only: bool,
    identity,
) -> str:
    """Search using ripgrep."""
    max_results = max(1, min(int(max_results), 500))
    context = max(0, min(int(context), 50))
    args = ["rg", "--no-heading", f"--max-count={max_results}", "--color=never"]
    # Never search .env files
    args.extend(["--glob", "!.env", "--glob", "!.env.*"])
    if not regex:
        args.append("--fixed-strings")
    if case_insensitive:
        args.append("--ignore-case")
    if word:
        args.append("--word-regexp")
    if multiline:
        args.append("--multiline")
    if context > 0:
        args.append(f"--context={context}")
    if filenames_only:
        args.append("--files-with-matches")
    else:
        args.append("--line-number")
    if type:
        for t in type.split(","):
            t = t.strip()
            if t:
                args.extend(["--type", t])
    if include:
        for glob in include.split(","):
            glob = glob.strip()
            if glob:
                args.extend(["--glob", glob])
    if exclude:
        for glob in exclude.split(","):
            glob = glob.strip()
            if glob:
                args.extend(["--glob", f"!{glob}"])

    args.extend(["--", query, str(full)])

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env_for(identity, full) if identity.is_pam else None,
        preexec_fn=preexec_for(identity) if identity.is_pam else None,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    output = stdout.decode(errors="replace").strip()

    if proc.returncode == 1:
        return "No matches found."
    if proc.returncode > 1:
        err = stderr.decode(errors="replace").strip()
        raise FileNotFoundError(err) if "not found" in err.lower() else Exception(err)

    # Make paths relative
    prefix = str(full) + os.sep
    lines = output.splitlines()[:max_results]
    result = [line.replace(prefix, "") for line in lines]
    return "\n".join(result) if result else "No matches found."


async def _search_python(query: str, full: Path, case_insensitive: bool) -> str:
    """Fallback search using pure Python (when ripgrep not installed)."""

    def _read_text_for_search(fpath: Path) -> str | None:
        """Read a file for searching, skipping binary-looking files.

        Match ripgrep's default binary handling closely enough for the fallback:
        files containing NUL bytes are treated as binary and are not decoded or
        returned as replacement-character text.
        """
        try:
            data = fpath.read_bytes()
        except (OSError, PermissionError):
            return None
        if b"\0" in data:
            return None
        return data.decode(errors="replace")

    def _walk_and_search():
        results = []
        ignore = {".git", "node_modules", "__pycache__", ".venv", "venv"}
        ignore_base, ignore_patterns = load_gitignore(full)
        q = query.lower() if case_insensitive else query

        for root, dirs, files in os.walk(full):
            root_path = Path(root)
            dirs[:] = [
                d
                for d in dirs
                if d not in ignore
                and not is_gitignored(root_path / d, ignore_base, ignore_patterns, is_dir=True)
            ]
            for fname in files:
                fpath = Path(root) / fname
                if _is_dotenv(fpath) or is_gitignored(
                    fpath, ignore_base, ignore_patterns, is_dir=False
                ):
                    continue
                text = _read_text_for_search(fpath)
                if text is None:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    target = line.lower() if case_insensitive else line
                    if q in target:
                        rel = fpath.relative_to(full)
                        results.append(f"{rel}:{i}: {line.strip()}")
                        if len(results) >= 50:
                            results.append("... (truncated at 50 matches)")
                            return "\n".join(results)

        return "\n".join(results) if results else "No matches found."

    return await asyncio.to_thread(_walk_and_search)


# ── Git tools ────────────────────────────────────────────────


def _git_workspace_root(__context__: dict) -> str:
    """Return the workspace path to run git commands against ('' if none)."""
    workspace = __context__.get("workspace") or ""
    if not workspace:
        return ""
    return str(Path(workspace).resolve())


async def _git_repo_root(__context__: dict) -> tuple[str, str | None, Any | None]:
    """Resolve workspace + identity for a git tool. Returns (root, error, identity)."""
    root = _git_workspace_root(__context__)
    if not root:
        return "", "Error: git tools require an open workspace.", None
    try:
        identity = await identity_for_context(__context__)
    except IdentityUnavailable as e:
        return "", f"Error: {e}", None
    return root, None, identity


async def git_status(*, __context__: dict) -> str:
    """Show git status for the current workspace: branch, upstream, ahead/behind, and changed files."""
    root, error, identity = await _git_repo_root(__context__)
    if error:
        return error
    try:
        if not await gitlib.is_repo(root, identity):
            return "Not a git repository."
        st = await gitlib.status(root, identity)
    except gitlib.GitError as e:
        return f"Error: {e}"

    lines = [f"On branch {st['branch'] or '(detached HEAD)'}"]
    if st["upstream"]:
        lines.append(f"Tracking {st['upstream']} (ahead {st['ahead']}, behind {st['behind']})")
    elif st["branch"]:
        lines.append("No upstream branch (unpublished)")

    files = st["files"]
    if not files:
        lines.append("Working tree clean")
    else:
        for f in files:
            flags = []
            if f.get("staged"):
                flags.append(f"staged:{f.get('staged_status', '')}")
            if f.get("unstaged"):
                flags.append(f"unstaged:{f.get('unstaged_status', '')}")
            counts = ""
            if "additions" in f and "deletions" in f:
                counts = f" (+{f['additions']}/-{f['deletions']})"
            lines.append(f"  {f['path']}{counts} [{', '.join(flags)}]")
    return _truncate_output("\n".join(lines), max_chars=CHAT_TOOL_MAX_CHARS)


async def git_log(
    limit: int = 30,
    offset: int = 0,
    path: str = "",
    grep: str = "",
    *,
    __context__: dict,
) -> str:
    """Show commit history for the current workspace's repository.

    :param limit: Number of commits to return (1-100).
    :param offset: Number of commits to skip (for paging).
    :param path: Restrict to commits touching this path (uses --follow).
    :param grep: Only include commits whose message matches this pattern.
    """
    root, error, identity = await _git_repo_root(__context__)
    if error:
        return error
    try:
        if not await gitlib.is_repo(root, identity):
            return "Not a git repository."
        commits = await gitlib.log(
            root,
            limit=max(1, min(int(limit), 100)),
            offset=max(0, int(offset)),
            path=path or None,
            grep=grep or None,
            identity=identity,
        )
    except gitlib.GitError as e:
        return f"Error: {e}"

    if not commits:
        return "No commits found."
    lines = [f"{c['short_hash']}  {c['author']}  {c['date']}  {c['message']}" for c in commits]
    return _truncate_output("\n".join(lines), max_chars=CHAT_TOOL_MAX_CHARS)


async def git_show(ref: str, *, __context__: dict) -> str:
    """Show a commit's metadata and patch.

    :param ref: Commit reference (hash, branch, or relative like HEAD~1).
    """
    root, error, identity = await _git_repo_root(__context__)
    if error:
        return error
    try:
        if not await gitlib.is_repo(root, identity):
            return "Not a git repository."
        out = await gitlib.show_readable(root, ref, identity=identity)
    except gitlib.GitError as e:
        return f"Error: {e}"
    return _truncate_output(out.strip() or "(no diff)", max_chars=CHAT_TOOL_MAX_CHARS)


async def git_diff(
    staged: bool = False,
    file: str = "",
    ref: str = "",
    untracked: bool = False,
    *,
    __context__: dict,
) -> str:
    """Show a diff for the current workspace's repository.

    :param staged: Show staged (index) changes instead of unstaged working-tree changes.
    :param file: Restrict the diff to this single file path.
    :param ref: Compare against a commit or range (e.g. 'HEAD~1', 'main...HEAD').
    :param untracked: Include an untracked file's content as a diff (requires file).
    """
    root, error, identity = await _git_repo_root(__context__)
    if error:
        return error
    try:
        if not await gitlib.is_repo(root, identity):
            return "Not a git repository."
        if ref:
            out = await gitlib.diff_ref_text(root, ref, identity=identity)
        else:
            out = await gitlib.diff_text(
                root, file=file or None, staged=staged, untracked=untracked, identity=identity
            )
    except gitlib.GitError as e:
        return f"Error: {e}"
    out = out.strip()
    return _truncate_output(out or "No changes.", max_chars=CHAT_TOOL_MAX_CHARS)


async def git_blame(file: str, *, __context__: dict) -> str:
    """Show who last changed each line of a file.

    :param file: File path relative to the workspace.
    """
    root, error, identity = await _git_repo_root(__context__)
    if error:
        return error
    if not file.strip():
        return "Error: file is required."
    try:
        if not await gitlib.is_repo(root, identity):
            return "Not a git repository."
        entries = await gitlib.blame(root, file, identity)
    except gitlib.GitError as e:
        return f"Error: {e}"

    lines = []
    for e in entries:
        lines.append(f"{e['line']:>6}  {e['hash'][:10]}  {e['author']:<20}  {e['text']}")
    return _truncate_output("\n".join(lines), max_chars=CHAT_TOOL_MAX_CHARS)


async def create_file(
    path: str = "",
    content: str = "",
    overwrite: bool = False,
    artifact_type: str = "",
    *,
    __context__: dict,
) -> str:
    """Create a new file, or create an artifact for user review.
    When artifact_type is set, path is optional. The artifact is saved automatically.
    :param path: Path relative to workspace root (optional for artifacts).
    :param content: File contents to write.
    :param overwrite: Set to true to overwrite an existing file.
    :param artifact_type: Set to 'implementation_plan' to present a plan for user review before coding.
    """
    workspace = __context__["workspace"]
    request = __context__.get("request")
    if request is None:
        return "Error: request context unavailable"

    # Artifact mode: save to .cptr/artifacts/ (same location as create_artifact)
    # When artifact_type is set, path is ignored.
    if artifact_type:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        artifact_dir = Path(workspace) / ".cptr" / "artifacts"
        artifact_path = artifact_dir / f"{ts}_{artifact_type}.md"

        try:
            if request is None:
                return "Error: request context unavailable"
            await Runtime.write_file(request, str(artifact_path), content)
        except FileError as e:
            return f"Error: {e}"

        rel_path = str(artifact_path.relative_to(Path(workspace)))
        display_title = artifact_type.replace("_", " ").title()
        return json.dumps(
            {
                "artifact_type": artifact_type,
                "title": display_title,
                "path": rel_path,
                "bytes": len(content),
            }
        )

    if not path:
        return "Error: path is required when artifact_type is not set."

    full = _resolve_path(path, workspace)
    if _is_dotenv(full):
        return _DOTENV_ERROR
    if full.is_file() and not overwrite:
        return f"Error: file already exists: {path}. Use overwrite=true or edit_file to modify."

    try:
        if request is None:
            return "Error: request context unavailable"
        await Runtime.write_file(request, str(full), content)
    except FileError as e:
        return f"Error: {e}"
    return f"Created {path} ({len(content)} bytes, {len(content.splitlines())} lines)"


async def create_artifact(
    content: str,
    artifact_type: str = "implementation_plan",
    title: str = "",
    *,
    __context__: dict,
) -> str:
    """Create an artifact for user review. Use for implementation plans and analysis.
    :param content: Artifact content as markdown.
    :param artifact_type: Type of artifact, e.g. 'implementation_plan'.
    :param title: Display title for the artifact card.
    """
    from datetime import datetime, timezone

    workspace = __context__["workspace"]
    request = __context__.get("request")
    if request is None:
        return "Error: request context unavailable"
    artifact_type = artifact_type or "implementation_plan"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    artifact_dir = Path(workspace) / ".cptr" / "artifacts"
    artifact_path = artifact_dir / f"{ts}_{artifact_type}.md"

    try:
        await Runtime.write_file(request, str(artifact_path), content)
    except FileError as e:
        return f"Error: {e}"

    display_title = title or artifact_type.replace("_", " ").title()
    rel_path = str(artifact_path.relative_to(Path(workspace)))
    return json.dumps(
        {
            "artifact_type": artifact_type,
            "title": display_title,
            "path": rel_path,
            "bytes": len(content),
        }
    )


def _normalize_tasks(tasks: Any, existing_tasks: Any = None, merge: bool = False) -> list[dict]:
    if isinstance(tasks, str):
        try:
            tasks = json.loads(tasks)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(tasks, list):
        return []

    existing = _normalize_tasks(existing_tasks) if merge else []
    by_id: dict[str, dict] = {task["id"]: task for task in existing}
    order: list[str] = [task["id"] for task in existing]
    next_index = len(order)
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id", "") or "").strip()
        current = by_id.get(task_id) if merge and task_id else None
        content_value = item.get("content")
        content = str(content_value).strip() if content_value is not None else ""
        if len(content) > MAX_TASK_CONTENT_CHARS:
            keep = MAX_TASK_CONTENT_CHARS - len(_TASK_TRUNCATION_MARKER)
            content = content[:keep] + _TASK_TRUNCATION_MARKER
        if current and not content:
            content = current["content"]
        if not content:
            continue
        status = str(item.get("status", current.get("status") if current else "pending")).lower()
        if status not in VALID_TASK_STATUSES:
            status = "pending"
        task_id = task_id or str(next_index + 1)
        if task_id in by_id and task_id in order:
            order.remove(task_id)
        else:
            next_index += 1
        by_id[task_id] = {"id": task_id, "content": content, "status": status}
        order.append(task_id)
    return [by_id[task_id] for task_id in order][:MAX_TASK_ITEMS]


async def update_tasks(
    tasks: list[dict[str, Any]],
    merge: bool = False,
    *,
    __context__: dict,
) -> str:
    """Update the visible Tasks list for this chat.
    :param tasks: Task items to show. Each item may include id, content, and status.
    :param merge: If true, update existing tasks by id and append new ones. If false, replace the list.
    """
    from cptr.models import Chat
    from cptr.socket.main import emit_to_user
    from cptr.utils.config import now_ms

    chat_id = __context__.get("chat_id")
    user_id = __context__.get("user_id")
    if not chat_id:
        return json.dumps({"error": "Chat context not available"})

    chat = await Chat.get_by_id(chat_id)
    if not chat:
        return json.dumps({"error": "Chat not found"})

    meta = dict(chat.meta or {})
    next_tasks = _normalize_tasks(tasks, meta.get("tasks"), merge=merge)
    summary = {
        "total": len(next_tasks),
        "pending": sum(1 for task in next_tasks if task["status"] == "pending"),
        "in_progress": sum(1 for task in next_tasks if task["status"] == "in_progress"),
        "completed": sum(1 for task in next_tasks if task["status"] == "completed"),
        "cancelled": sum(1 for task in next_tasks if task["status"] == "cancelled"),
    }
    meta["tasks"] = next_tasks
    await Chat.update_meta(chat_id, meta, now_ms())

    if user_id:
        await emit_to_user(
            user_id,
            {
                "type": "chat:tasks",
                "chat_id": chat_id,
                "message_id": __context__.get("message_id"),
                "tasks": next_tasks,
                "summary": summary,
            },
        )

    return json.dumps({"tasks": next_tasks, "summary": summary}, ensure_ascii=False)


async def clear_active_tasks(
    chat_id: str, user_id: str | None = None, message_id: str | None = None
) -> None:
    from cptr.models import Chat
    from cptr.socket.main import emit_to_user
    from cptr.utils.config import now_ms

    chat = await Chat.get_by_id(chat_id)
    if not chat:
        return
    meta = dict(chat.meta or {})
    tasks = _normalize_tasks(meta.get("tasks"))
    if not any(task["status"] in {"pending", "in_progress"} for task in tasks):
        return
    meta["tasks"] = []
    await Chat.update_meta(chat_id, meta, now_ms())
    if user_id:
        await emit_to_user(
            user_id,
            {
                "type": "chat:tasks",
                "chat_id": chat_id,
                "message_id": message_id,
                "tasks": [],
                "summary": {
                    "total": 0,
                    "pending": 0,
                    "in_progress": 0,
                    "completed": 0,
                    "cancelled": 0,
                },
            },
        )


async def write_file(path: str, content: str, *, __context__: dict) -> str:
    """Write or create a file (full content). Prefer edit_file for modifications.
    :param path: Path relative to workspace root.
    :param content: File contents to write.
    """
    workspace = __context__["workspace"]
    request = __context__.get("request")
    if request is None:
        return "Error: request context unavailable"
    full = _resolve_path(path, workspace)
    if _is_dotenv(full):
        return _DOTENV_ERROR

    try:
        if request is None:
            return "Error: request context unavailable"
        await Runtime.write_file(request, str(full), content)
    except FileError as e:
        return f"Error: {e}"
    return f"Wrote {len(content)} bytes to {path}"


async def display_file(path: str, *, __context__: dict) -> str:
    """Display a workspace file inline in chat.
    Use when the user asks to see, preview, render, or display a file you created or found.
    :param path: Path relative to workspace root.
    """
    if not path:
        return "Error: path is required."
    workspace = __context__["workspace"]
    request = __context__.get("request")
    if request is None:
        return "Error: request context unavailable"

    try:
        full = _resolve_path(path, workspace)
    except ValueError as exc:
        return f"Error: {exc}"

    if _is_dotenv(full):
        return _DOTENV_ERROR
    try:
        file_stat = await Runtime.stat(request, str(full))
    except FileError:
        return f"Error: file not found: {path}"
    if file_stat.get("type") != "file":
        return f"Error: not a file: {path}"

    mime_type = mimetypes.guess_type(str(full))[0] or "application/octet-stream"
    ws = Path(workspace).resolve()
    try:
        display_path = str(full.relative_to(ws))
    except ValueError:
        display_path = str(full)
    return json.dumps(
        {
            "type": "file",
            "path": display_path,
            "full_path": str(full),
            "workspace": str(ws),
            "name": full.name,
            "size": file_stat.get("size") or 0,
            "mime_type": mime_type,
            "kind": _file_kind(full, mime_type),
        },
        ensure_ascii=False,
    )


async def create_download_link(path: str, name: str = "", *, __context__: dict) -> str:
    """Create a download link the user clicks to save a file to their own computer.
    The file lives on this runtime; the link hands it to the browser's machine, not the
    server. Use when the user asks to download, save, export, or send over a file. Single
    files only: archive a directory (tar/zip) first and link the archive.
    :param path: Path of the file to hand over (relative to workspace root, or absolute).
    :param name: Optional filename to save as (defaults to the file's own name).
    """
    if not path:
        return "Error: path is required."
    workspace = __context__["workspace"]
    request = __context__.get("request")
    if request is None:
        return "Error: request context unavailable"

    try:
        full = _resolve_path(path, workspace)
    except ValueError as exc:
        return f"Error: {exc}"

    if _is_dotenv(full):
        return _DOTENV_ERROR

    try:
        file_stat = await Runtime.stat(request, str(full))
    except FileError:
        return f"Error: file not found: {path}"
    if file_stat.get("type") == "directory":
        return (
            f"Error: {path} is a directory. Archive it first "
            "(e.g. `tar -czf archive.tar.gz <dir>`, or python's zipfile), then link the archive."
        )
    if file_stat.get("type") != "file":
        return f"Error: not a file: {path}"

    display_name = _safe_download_name(name) or full.name
    params = {"path": str(full)}
    if display_name != full.name:
        params["filename"] = display_name

    ws = Path(workspace).resolve()
    try:
        display_path = str(full.relative_to(ws))
    except ValueError:
        display_path = str(full)
    return json.dumps(
        {
            "type": "download",
            "url": f"/api/workspace/files/download?{urlencode(params)}",
            "name": display_name,
            "path": display_path,
            "full_path": str(full),
            "workspace": str(ws),
            "size": file_stat.get("size") or 0,
            "mime_type": mimetypes.guess_type(display_name)[0] or "application/octet-stream",
        },
        ensure_ascii=False,
    )


async def edit_file(
    path: str,
    target: str,
    replacement: str,
    start_line: int = 0,
    end_line: int = 0,
    *,
    __context__: dict,
) -> str:
    """Replace a specific text block in a file. Only provide the text that changes.
    :param path: Path relative to workspace root.
    :param target: Exact text to find and replace (must match file content exactly).
    :param replacement: Text to replace the target with.
    :param start_line: Narrow search to lines starting here (1-indexed, 0 = from start).
    :param end_line: Narrow search to lines ending here (0 = to end).
    """
    workspace = __context__["workspace"]
    request = __context__.get("request")
    if request is None:
        return "Error: request context unavailable"
    full = _resolve_path(path, workspace)
    if _is_dotenv(full):
        return _DOTENV_ERROR
    try:
        file_data = await Runtime.read_file(request, str(full))
    except FileError:
        return f"Error: file not found: {path}"
    if file_data.get("binary"):
        return f"Error: not a text file: {path}"

    content = str(file_data.get("content") or "")

    if start_line > 0 or end_line > 0:
        lines = content.splitlines(keepends=True)
        total = len(lines)
        s = max(1, start_line) - 1
        e = min(total, end_line) if end_line > 0 else total
        region = "".join(lines[s:e])

        if target not in region:
            return f"Error: target text not found in lines {s + 1}-{e} of {path}"

        count = region.count(target)
        if count > 1:
            return (
                f"Error: target text found {count} times in lines {s + 1}-{e}. "
                f"Narrow the line range or use a more specific target."
            )

        new_region = region.replace(target, replacement, 1)
        new_content = "".join(lines[:s]) + new_region + "".join(lines[e:])
    else:
        count = content.count(target)
        if count == 0:
            return f"Error: target text not found in {path}"
        if count > 1:
            return (
                f"Error: target text found {count} times in {path}. "
                f"Use start_line/end_line to disambiguate."
            )
        new_content = content.replace(target, replacement, 1)

    try:
        await Runtime.write_file(request, str(full), new_content)
    except FileError as e:
        return f"Error: {e}"

    target_lines = len(target.splitlines())
    replacement_lines = len(replacement.splitlines())
    return (
        f"Edited {path}: replaced {target_lines} lines with {replacement_lines} lines "
        f"({len(target)} chars → {len(replacement)} chars)"
    )


async def multi_edit_file(
    path: str,
    edits: str,
    *,
    __context__: dict,
) -> str:
    """Apply multiple non-contiguous edits to a file in one operation.
    :param path: Path relative to workspace root.
    :param edits: JSON array of edit objects, each with 'target' and 'replacement' strings, and optional 'start_line'/'end_line' integers.
    """
    workspace = __context__["workspace"]
    request = __context__.get("request")
    if request is None:
        return "Error: request context unavailable"
    full = _resolve_path(path, workspace)
    if _is_dotenv(full):
        return _DOTENV_ERROR
    try:
        file_data = await Runtime.read_file(request, str(full))
    except FileError:
        return f"Error: file not found: {path}"
    if file_data.get("binary"):
        return f"Error: not a text file: {path}"

    try:
        edit_list = json.loads(edits)
    except json.JSONDecodeError as e:
        return f"Error: invalid edits JSON: {e}"

    if not isinstance(edit_list, list) or not edit_list:
        return "Error: edits must be a non-empty JSON array"

    content = str(file_data.get("content") or "")
    applied = 0

    for i, edit in enumerate(edit_list):
        target = edit.get("target", "")
        replacement = edit.get("replacement", "")

        if not target:
            return f"Error: edit {i + 1} missing 'target'"

        if target not in content:
            return f"Error: target not found for edit {i + 1}: {target[:100]}..."

        count = content.count(target)
        if count > 1:
            return (
                f"Error: edit {i + 1} target found {count} times. "
                f"Each target must be unique in the file."
            )

        content = content.replace(target, replacement, 1)
        applied += 1

    try:
        await Runtime.write_file(request, str(full), content)
    except FileError as e:
        return f"Error: {e}"
    return f"Applied {applied} edits to {path}"


async def run_command(
    command: str,
    cwd: str = ".",
    wait: Optional[int] = None,
    *,
    __context__: dict,
) -> str:
    """Run a shell command. Returns a task_id for status checks and input.
    :param command: The shell command to execute.
    :param cwd: Working directory relative to workspace root.
    :param wait: Seconds to wait for the command to finish before returning (max 300). Returns early if done sooner. Null returns immediately. Use 30-60 for installs and builds, 5-10 for quick commands, null or 0 for long-lived servers.
    """
    workspace = __context__["workspace"]
    try:
        identity = await identity_for_context(__context__)
    except IdentityUnavailable as e:
        return f"Error: {e}"
    user_id = identity.app_user_id or __context__.get("user_id")
    request = __context__.get("request")

    work_dir = _resolve_path(cwd, workspace)
    if not work_dir.is_dir():
        return f"Error: not a directory: {cwd}"

    active = sum(
        1
        for t in command_sessions.values()
        if not t.get("done") and (user_id is None or t.get("user_id") == user_id)
    )
    if active >= MAX_COMMAND_SESSIONS:
        return f"Error: too many running command sessions ({active}/{MAX_COMMAND_SESSIONS}). Stop one first."

    if identity.is_pam:
        env = env_for(identity, work_dir, {"PAGER": "cat", "GIT_PAGER": "cat"})
        preexec = preexec_for(identity)
    else:
        env = {**os.environ, "PAGER": "cat", "GIT_PAGER": "cat"}
        preexec = None
    master_fd = None

    try:
        if _PTY_AVAILABLE:
            proc, master_fd = _spawn_pty(command, str(work_dir), env, preexec)
        else:
            kwargs = {}
            if preexec is not None:
                kwargs["preexec_fn"] = preexec
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
                env=env,
                **kwargs,
            )
    except Exception as e:
        return f"Error: {e}"

    command_session_id = uuid.uuid4().hex[:8]
    log_path = Path(workspace) / ".cptr" / "task_logs" / f"{command_session_id}.jsonl"
    try:
        if request is None:
            return "Error: request context unavailable"
        await Runtime.write_file(request, str(log_path), "")
    except FileError as e:
        try:
            _kill_process_group(proc.pid)
        except Exception:
            pass
        return f"Error: {e}"

    command_sessions[command_session_id] = {
        "command_session_id": command_session_id,
        "master_fd": master_fd,
        "proc": proc,
        "output": bytearray(),
        "total_bytes": 0,
        "command": command,
        "workspace": workspace,
        "user_id": user_id,
        "identity": identity,
        "chat_id": __context__.get("chat_id"),
        "message_id": __context__.get("message_id"),
        "call_id": __context__.get("call_id"),
        "created_at": time.time(),
        "done": False,
        "exit_code": None,
        "log_path": str(log_path),
        "log_task": None,
        "condition": asyncio.Condition(),
    }
    log_task = asyncio.create_task(stream_command_session_output(command_session_id))
    command_sessions[command_session_id]["log_task"] = log_task

    # Wait for the command to finish inline (matches open-terminal behaviour)
    if wait is None and EXECUTE_TIMEOUT:
        wait = EXECUTE_TIMEOUT
    if wait is not None and wait > 0:
        try:
            await asyncio.wait_for(asyncio.shield(log_task), timeout=min(wait, 300))
        except asyncio.TimeoutError:
            pass

    task = command_sessions.get(command_session_id)
    output = task["output"].decode(errors="replace") if task else ""
    output = _truncate_output(output, max_chars=CHAT_TOOL_COMMAND_MAX_CHARS)
    done = task.get("done", False) if task else True
    exit_code = task.get("exit_code") if task else None
    next_offset = task.get("total_bytes", 0) if task else 0

    if done:
        status = f"exited (code {exit_code})"
    else:
        status = "running"

    return f"Task {command_session_id}: {status}\nCommand: {command}\nnext_offset: {next_offset}\n---\n{output}"


async def check_task(
    task_id: str, offset: int = 0, wait: Optional[int] = None, *, __context__: dict
) -> str:
    """Check status and recent output of a background task.
    :param task_id: The task ID returned by run_command.
    :param offset: Byte offset from previous check. Pass next_offset from the last response to get only new output.
    :param wait: Seconds to wait for the task to finish before returning (max 300). Returns early if done sooner. Null returns immediately.
    """
    request = __context__.get("request")
    task = get_command_session(request, task_id, context=__context__)
    if not task:
        user_id = __context__.get("user_id")
        available = [
            session_id
            for session_id, session in command_sessions.items()
            if session.get("user_id") == user_id
        ]
        return f"Error: no task with id '{task_id}'. Active tasks: {available or 'none'}"

    # Optionally wait for the task to finish
    if wait is None and EXECUTE_TIMEOUT:
        wait = EXECUTE_TIMEOUT
    if wait is not None and wait > 0 and not task.get("done"):
        collect = task.get("log_task")
        if collect and not collect.done():
            try:
                await asyncio.wait_for(asyncio.shield(collect), timeout=min(wait, 300))
            except asyncio.TimeoutError:
                pass

    buf = task["output"]
    total = task.get("total_bytes", 0)
    buf_start = total - len(buf)  # byte offset of first byte in buffer

    if offset <= buf_start:
        # Requested offset is before buffer start (old output was trimmed)
        raw = buf
    else:
        # Slice to only return new output since offset
        skip = offset - buf_start
        raw = buf[skip:]

    output = raw.decode(errors="replace")
    output = _truncate_output(output, max_chars=CHAT_TOOL_COMMAND_MAX_CHARS)
    next_offset = total

    if task.get("done", False):
        status = f"exited (code {task.get('exit_code')})"
    else:
        status = "running"

    return f"Task {task_id}: {status}\nCommand: {task['command']}\nnext_offset: {next_offset}\n---\n{output}"


async def kill_task(task_id: str, force: bool = False, *, __context__: dict) -> str:
    """Terminate a running task. Sends SIGTERM for graceful shutdown by default.
    :param task_id: The task ID to kill.
    :param force: Send SIGKILL instead of SIGTERM for immediate termination.
    """
    request = __context__.get("request")
    task = get_command_session(request, task_id, context=__context__)
    if not task:
        user_id = __context__.get("user_id")
        available = [
            session_id
            for session_id, session in command_sessions.items()
            if session.get("user_id") == user_id
        ]
        return f"Error: no task with id '{task_id}'. Active tasks: {available or 'none'}"

    if task.get("done", False):
        exit_code = task.get("exit_code")
        return f"Task {task_id} already finished (code {exit_code})"

    stop_command_session(request, task_id, force=force, context=__context__)

    action = "Killed" if force else "Terminated"
    return f"{action} task {task_id}"


async def send_input(task_id: str, input: str, *, __context__: dict) -> str:
    """Send input to a running task's stdin. Use for interactive prompts, REPLs, or control characters.
    :param task_id: The task ID returned by run_command.
    :param input: Text to send. Use \\n for Enter, \\x03 for Ctrl-C, \\x04 for Ctrl-D.
    """
    request = __context__.get("request")
    task = get_command_session(request, task_id, context=__context__)
    if not task:
        user_id = __context__.get("user_id")
        available = [
            session_id
            for session_id, session in command_sessions.items()
            if session.get("user_id") == user_id
        ]
        return f"Error: no task '{task_id}'. Active: {available or 'none'}"

    if task.get("done", False):
        return f"Error: task {task_id} already exited (code {task.get('exit_code')})"

    # LLMs emit literal "\n" — convert to real characters
    try:
        text = input.encode("raw_unicode_escape").decode("unicode_escape")
    except (UnicodeDecodeError, ValueError):
        text = input

    error = send_command_session_input(request, task_id, text.encode(), context=__context__)
    if error:
        return f"Error: {error} for task {task_id}"
    await drain_command_session_input(request, task_id, context=__context__)

    return f"Sent {len(text)} bytes to task {task_id}"


async def web_search(query: str, *, workspace: str) -> str:
    """Search the web for information. Returns summaries with source URLs.
    :param query: The search query.
    """
    # Defer to web module
    from cptr.utils.web import web_search_handler

    return await web_search_handler(query)


async def read_url(url: str, *, workspace: str) -> str:
    """Fetch content from a URL and return as text. Converts HTML to readable text.
    :param url: The URL to fetch.
    """
    from cptr.utils.web import read_url_handler

    return await read_url_handler(url)


# ── Path safety ──────────────────────────────────────────────


def _resolve_path(path: str, workspace: str) -> Path:
    """Resolve a path within the workspace or uploads dir. Rejects traversal."""
    from cptr.utils.storage import UPLOADS_DIR

    p = Path(path)
    # Allow absolute paths to the uploads directory (for user-attached files)
    if p.is_absolute():
        full = p.resolve()
        uploads = UPLOADS_DIR.resolve()
        ws = Path(workspace).resolve()
        if full.is_relative_to(uploads) or full.is_relative_to(ws):
            return full
        raise ValueError(f"Path outside allowed directories: {path}")

    # Relative paths resolve against workspace
    ws = Path(workspace).resolve()
    full = (ws / path).resolve()
    if not full.is_relative_to(ws):
        raise ValueError(f"Path traversal rejected: {path}")
    return full


async def create_automation(
    name: str,
    prompt: str,
    rrule: str,
    *,
    __context__: dict,
) -> str:
    """Create a scheduled automation that runs a prompt on a recurring or one-time schedule.
    The rrule parameter must be a valid iCalendar RRULE string. Common examples:
    - Every day at 9am: "DTSTART:20250101T090000\\nRRULE:FREQ=DAILY"
    - Every Monday at 8am: "DTSTART:20250106T080000\\nRRULE:FREQ=WEEKLY;BYDAY=MO"
    - Every hour: "RRULE:FREQ=HOURLY;INTERVAL=1"
    - Once at a specific time: "DTSTART:20250415T140000\\nRRULE:FREQ=DAILY;COUNT=1"
    - First day of every month: "DTSTART:20250101T090000\\nRRULE:FREQ=MONTHLY;BYMONTHDAY=1"
    :param name: A short descriptive name for the automation.
    :param prompt: The instructions/prompt to execute on each run.
    :param rrule: An iCalendar RRULE string defining the schedule.
    """
    workspace = __context__["workspace"]
    user_id = __context__["user_id"]
    model_id = __context__.get("model_id", "")

    try:
        import time
        from cptr.models.automations import Automation as AutomationModel
        from cptr.utils.automations import next_run_ns, next_n_runs_ns, validate_rrule

        # Validate RRULE
        try:
            validate_rrule(rrule)
        except ValueError as e:
            return json.dumps({"error": f"Invalid schedule: {e}"})

        if not model_id:
            return json.dumps({"error": "Could not detect model from current chat context."})

        now_ns = int(time.time() * 1_000_000_000)
        nxt = next_run_ns(rrule)

        automation = await AutomationModel.create(
            user_id=user_id,
            name=name,
            prompt=prompt,
            model_id=model_id,
            workspace=workspace,
            rrule=rrule,
            next_run_at=nxt,
            is_active=True,
            created_at=now_ns,
        )
        return json.dumps(
            {
                "status": "success",
                "id": automation.id,
                "name": automation.name,
                "model_id": automation.model_id,
                "is_active": automation.is_active,
                "next_runs": next_n_runs_ns(rrule),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def list_automations(
    status: str = "",
    count: int = 10,
    *,
    __context__: dict,
) -> str:
    """List scheduled automations for the current workspace.
    :param status: Filter by status: "active", "paused", or empty for all.
    :param count: Maximum number of automations to return (default: 10).
    """
    workspace = __context__["workspace"]
    user_id = __context__["user_id"]

    try:
        from cptr.models.automations import Automation as AutomationModel
        from cptr.utils.automations import next_n_runs_ns

        items, total = await AutomationModel.get_by_workspace(
            user_id=user_id,
            workspace=workspace or None,
            status=status or None,
            limit=count,
        )
        automations = []
        for item in items:
            automations.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "prompt_snippet": item.prompt[:100] + ("..." if len(item.prompt) > 100 else ""),
                    "model_id": item.model_id,
                    "rrule": item.rrule,
                    "is_active": item.is_active,
                    "last_run_at": item.last_run_at,
                    "next_runs": next_n_runs_ns(item.rrule),
                }
            )
        return json.dumps({"automations": automations, "total": total})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def update_automation(
    automation_id: str,
    name: str = "",
    prompt: str = "",
    rrule: str = "",
    model_id: str = "",
    *,
    __context__: dict,
) -> str:
    """Update an existing automation. Only provided fields are changed.
    :param automation_id: The ID of the automation to update.
    :param name: New name (optional).
    :param prompt: New prompt/instructions (optional).
    :param rrule: New iCalendar RRULE schedule string (optional).
    :param model_id: New model ID (optional).
    """
    try:
        import time
        from cptr.models.automations import Automation as AutomationModel
        from cptr.utils.automations import next_run_ns, next_n_runs_ns, validate_rrule

        automation = await AutomationModel.get_by_id(automation_id)
        if not automation:
            return json.dumps({"error": "Automation not found"})

        kwargs = {}
        if name:
            kwargs["name"] = name
        if prompt:
            kwargs["prompt"] = prompt
        if model_id:
            kwargs["model_id"] = model_id
        if rrule:
            try:
                validate_rrule(rrule)
            except ValueError as e:
                return json.dumps({"error": f"Invalid schedule: {e}"})
            kwargs["rrule"] = rrule
            kwargs["next_run_at"] = next_run_ns(rrule)

        if not kwargs:
            return json.dumps({"error": "No fields to update"})

        now_ns = int(time.time() * 1_000_000_000)
        success = await AutomationModel.update_by_id(automation_id, updated_at=now_ns, **kwargs)
        if not success:
            return json.dumps({"error": "Failed to update automation"})

        final_rrule = rrule or automation.rrule
        return json.dumps(
            {
                "status": "success",
                "id": automation_id,
                "updated_fields": list(kwargs.keys()),
                "next_runs": next_n_runs_ns(final_rrule),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def toggle_automation(
    automation_id: str,
    *,
    __context__: dict,
) -> str:
    """Pause or resume a scheduled automation. If active, it will be paused. If paused, it will be resumed.
    :param automation_id: The ID of the automation to toggle.
    """
    try:
        from cptr.models.automations import Automation as AutomationModel
        from cptr.utils.automations import next_run_ns

        automation = await AutomationModel.get_by_id(automation_id)
        if not automation:
            return json.dumps({"error": "Automation not found"})

        nxt = next_run_ns(automation.rrule) if not automation.is_active else None
        toggled = await AutomationModel.toggle(automation_id, next_run_at=nxt)
        if not toggled:
            return json.dumps({"error": "Failed to toggle automation"})

        return json.dumps(
            {
                "status": "success",
                "id": toggled.id,
                "name": toggled.name,
                "is_active": toggled.is_active,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def delete_automation(
    automation_id: str,
    *,
    __context__: dict,
) -> str:
    """Delete a scheduled automation and all its run history.
    :param automation_id: The ID of the automation to delete.
    """
    try:
        from cptr.models.automations import Automation as AutomationModel

        automation = await AutomationModel.get_by_id(automation_id)
        if not automation:
            return json.dumps({"error": "Automation not found"})

        name = automation.name
        success = await AutomationModel.delete(automation_id)
        if not success:
            return json.dumps({"error": "Failed to delete automation"})

        return json.dumps({"status": "success", "message": f'Automation "{name}" deleted'})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Workspace todo tools ────────────────────────────────────
#
# Chats never mutate todos directly. Every add/complete/reopen/remove is
# recorded as a TodoRequest and lands in the dashboard "awaiting verification"
# queue, where the human approves or rejects it.


async def list_workspace_todos(
    *,
    __context__: dict,
) -> str:
    """List the todos for the current workspace, with their status and IDs.
    Use this before proposing changes so you reference real todo IDs.
    """
    workspace = __context__["workspace"]
    user_id = __context__["user_id"]

    try:
        from cptr.models.todos import WorkspaceTodo

        todos = await WorkspaceTodo.list_for_workspace(user_id, workspace)
        return json.dumps(
            {
                "workspace": workspace,
                "todos": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "status": t.status,
                        "source": t.source,
                    }
                    for t in todos
                ],
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def add_workspace_todo(
    title: str,
    *,
    __context__: dict,
) -> str:
    """Propose a new todo for the current workspace. The todo is NOT added
    immediately — it is queued for the human to verify in the dashboard.
    :param title: The todo text.
    """
    workspace = __context__["workspace"]
    user_id = __context__["user_id"]

    try:
        title = (title or "").strip()
        if not title:
            return json.dumps({"error": "title is required"})

        from cptr.models.todos import TodoRequest
        from cptr.utils.config import now_ms
        from cptr.socket.main import emit_todos_changed

        req = await TodoRequest.create(
            user_id=user_id,
            workspace=workspace,
            action="add",
            created_at=now_ms(),
            title=title,
        )
        await emit_todos_changed(user_id, workspace)
        return json.dumps(
            {
                "status": "pending_verification",
                "request_id": req.id,
                "message": (
                    f'Todo "{title}" proposed. It will appear in the workspace '
                    "dashboard under \"awaiting verification\" for the human to approve."
                ),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def complete_workspace_todo(
    todo_id: str,
    *,
    __context__: dict,
) -> str:
    """Propose marking a workspace todo as complete. The change is NOT applied
    immediately — it is queued for the human to verify in the dashboard.
    :param todo_id: The ID of the todo (from list_workspace_todos).
    """
    workspace = __context__["workspace"]
    user_id = __context__["user_id"]

    try:
        from cptr.models.todos import TodoRequest, WorkspaceTodo
        from cptr.utils.config import now_ms
        from cptr.socket.main import emit_todos_changed

        todo = await WorkspaceTodo.get_by_id(todo_id)
        if not todo or todo.user_id != user_id or todo.workspace != workspace:
            return json.dumps({"error": "Todo not found in this workspace"})
        if todo.status == "done":
            return json.dumps({"error": "Todo is already done"})

        req = await TodoRequest.create(
            user_id=user_id,
            workspace=workspace,
            action="complete",
            created_at=now_ms(),
            todo_id=todo_id,
            title=todo.title,
        )
        await emit_todos_changed(user_id, workspace)
        return json.dumps(
            {
                "status": "pending_verification",
                "request_id": req.id,
                "message": (
                    f'Marking "{todo.title}" complete is proposed and awaits '
                    "the human's approval in the workspace dashboard."
                ),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def reopen_workspace_todo(
    todo_id: str,
    *,
    __context__: dict,
) -> str:
    """Propose reopening (un-completing) a workspace todo. The change is NOT
    applied immediately — it is queued for the human to verify in the dashboard.
    :param todo_id: The ID of the todo (from list_workspace_todos).
    """
    workspace = __context__["workspace"]
    user_id = __context__["user_id"]

    try:
        from cptr.models.todos import TodoRequest, WorkspaceTodo
        from cptr.utils.config import now_ms
        from cptr.socket.main import emit_todos_changed

        todo = await WorkspaceTodo.get_by_id(todo_id)
        if not todo or todo.user_id != user_id or todo.workspace != workspace:
            return json.dumps({"error": "Todo not found in this workspace"})
        if todo.status == "open":
            return json.dumps({"error": "Todo is already open"})

        req = await TodoRequest.create(
            user_id=user_id,
            workspace=workspace,
            action="reopen",
            created_at=now_ms(),
            todo_id=todo_id,
            title=todo.title,
        )
        await emit_todos_changed(user_id, workspace)
        return json.dumps(
            {
                "status": "pending_verification",
                "request_id": req.id,
                "message": (
                    f'Reopening "{todo.title}" is proposed and awaits the '
                    "human's approval in the workspace dashboard."
                ),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def remove_workspace_todo(
    todo_id: str,
    *,
    __context__: dict,
) -> str:
    """Propose removing a workspace todo. The todo is NOT removed immediately —
    it is queued for the human to verify in the dashboard.
    :param todo_id: The ID of the todo (from list_workspace_todos).
    """
    workspace = __context__["workspace"]
    user_id = __context__["user_id"]

    try:
        from cptr.models.todos import TodoRequest, WorkspaceTodo
        from cptr.utils.config import now_ms
        from cptr.socket.main import emit_todos_changed

        todo = await WorkspaceTodo.get_by_id(todo_id)
        if not todo or todo.user_id != user_id or todo.workspace != workspace:
            return json.dumps({"error": "Todo not found in this workspace"})

        req = await TodoRequest.create(
            user_id=user_id,
            workspace=workspace,
            action="remove",
            created_at=now_ms(),
            todo_id=todo_id,
            title=todo.title,
        )
        await emit_todos_changed(user_id, workspace)
        return json.dumps(
            {
                "status": "pending_verification",
                "request_id": req.id,
                "message": (
                    f'Removing "{todo.title}" is proposed and awaits the '
                    "human's approval in the workspace dashboard."
                ),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Skill tools ─────────────────────────────────────────────

# Track activated skills per session (cleared on import)
_activated_skills: set[str] = set()


async def view_skill(
    skill_name: str,
    file_path: str = "",
    *,
    workspace: str,
) -> str:
    """Load the full instructions and resource listing for an available skill.
    :param skill_name: The name of the skill to load (from the <available_skills> catalog).
    :param file_path: Optional relative path to a bundled skill file (for example references/api.md).
    """
    from cptr.models import Config
    from cptr.utils.skills import bump_skill_view, load_skill, format_skill_content

    if (await Config.get("skills.enabled")) in (False, "false", "0"):
        return "Error: skills are disabled by the administrator."

    skill = load_skill(workspace, skill_name)
    if not skill:
        return f"Error: skill '{skill_name}' not found. Check <available_skills> for valid names."

    if file_path:
        skill_dir = Path(skill.location).parent
        p = Path(file_path)
        windows_path = PureWindowsPath(file_path)
        if p.is_absolute() or windows_path.is_absolute() or windows_path.drive:
            return "Error: skill file path must be relative to the skill directory."
        if ".." in p.parts or ".." in windows_path.parts:
            return "Error: skill file path cannot contain '..' traversal components."

        target = (skill_dir / file_path).resolve()
        try:
            target.relative_to(skill_dir.resolve())
        except (ValueError, OSError):
            return "Error: skill file path escapes the skill directory."
        if _is_dotenv(target):
            return _DOTENV_ERROR
        if not target.is_file():
            return f"Error: skill file not found: {file_path}"
        if target.suffix.lower() in IMAGE_EXTENSIONS:
            return await asyncio.to_thread(_read_image_file, target, file_path)

        def _read_skill_file():
            size = target.stat().st_size
            if size > 500_000:
                return f"Error: skill file too large ({size} bytes, max 500KB)"
            try:
                content = target.read_text(errors="strict")
            except (UnicodeDecodeError, ValueError):
                return f"Error: binary skill file ({target.suffix}), cannot read as text"
            return f'<skill_file name="{skill.name}" path="{file_path}">\n{content}\n</skill_file>'

        return await asyncio.to_thread(_read_skill_file)

    # Deduplication: if already activated, return short notice
    if skill_name in _activated_skills:
        return f"Skill '{skill_name}' is already loaded in this session. Refer to the existing <skill_content> above."

    _activated_skills.add(skill_name)
    bump_skill_view(workspace, skill_name, skill.source)
    return format_skill_content(skill)


async def manage_skill(
    action: Literal["create", "update", "write_file", "delete"],
    name: str,
    content: Optional[str] = None,
    scope: Literal["workspace", "global"] = "workspace",
    file_path: Optional[str] = None,
    file_content: Optional[str] = None,
    *,
    __context__: dict,
) -> str:
    """Create, update, or delete Computer-managed skills and supporting bundle files.

    Use this only when the user asks to create, update, or delete a reusable skill.
    New skills default to the current workspace. For supporting files, write only
    under references/, templates/, scripts/, or assets/.
    :param action: "create" to write SKILL.md, "update" to replace SKILL.md, "write_file" to add a bundle file, or "delete" to remove a managed skill.
    :param name: Lowercase hyphenated skill name.
    :param content: Full SKILL.md content for action="create" or action="update".
    :param scope: "workspace" for .cptr/skills, or "global" for ~/.cptr/skills.
    :param file_path: Relative bundle path for action="write_file".
    :param file_content: File content for action="write_file".
    """
    from cptr.utils.skills import (
        create_managed_skill,
        delete_managed_skill,
        update_managed_skill,
        write_managed_skill_file,
    )
    from cptr.models import Config

    if (await Config.get("skills.enabled")) in (False, "false", "0"):
        return json.dumps({"success": False, "error": "skills are disabled"}, ensure_ascii=False)
    if (await Config.get("skills.tool_enabled")) in (False, "false", "0"):
        return json.dumps(
            {"success": False, "error": "skill management is disabled"},
            ensure_ascii=False,
        )

    workspace = __context__.get("workspace", "")
    try:
        if action == "create":
            result = create_managed_skill(workspace, name, content or "", scope)
        elif action == "update":
            result = update_managed_skill(workspace, name, content or "")
        elif action == "write_file":
            result = write_managed_skill_file(workspace, name, file_path or "", file_content)
        elif action == "delete":
            result = delete_managed_skill(workspace, name)
        else:
            result = {"success": False, "error": f"unsupported action '{action}'"}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    return json.dumps(result, ensure_ascii=False)


# ── Browser tools ────────────────────────────────────────────


async def _get_browser_config() -> dict:
    """Read browser config from DB."""
    try:
        from cptr.models import Config

        return {
            "enabled": await Config.get("browser.enabled") or False,
            "provider": await Config.get("browser.provider") or "local",
            "cdp_url": await Config.get("browser.cdp_url") or "http://localhost:9222",
            "auto_launch": await Config.get("browser.auto_launch")
            if await Config.get("browser.auto_launch") is not None
            else True,
            "session_timeout": int(await Config.get("browser.session_timeout_minutes") or 10),
            "firecrawl_api_key": await Config.get("browser.firecrawl_api_key") or "",
            "firecrawl_base_url": await Config.get("browser.firecrawl_base_url")
            or "https://api.firecrawl.dev",
            "browser_use_api_key": await Config.get("browser.browser_use_api_key") or "",
            "browser_use_base_url": await Config.get("browser.browser_use_base_url")
            or "https://api.browser-use.com",
        }
    except Exception:
        return {"enabled": False, "provider": "local"}


async def _get_cdp_session(chat_id: str):
    """Get or create a CDP session for the current chat."""
    cfg = await _get_browser_config()
    cdp_url = cfg["cdp_url"]

    if cfg.get("auto_launch", True):
        from cptr.utils.browser.launcher import ensure_browser

        cdp_url = await ensure_browser(port=int(cdp_url.split(":")[-1]))

    from cptr.utils.browser.session import session_manager

    session_manager.set_timeout(cfg.get("session_timeout", 10))
    return await session_manager.get_or_create(chat_id, cdp_url)


async def browser_navigate(url: str, *, __context__: dict) -> str:
    """Navigate to a URL in the browser. Returns the page title and status.
    :param url: The URL to navigate to.
    """
    cfg = await _get_browser_config()
    provider = cfg.get("provider", "local")

    if provider == "firecrawl":
        key = cfg.get("firecrawl_api_key", "")
        if not key:
            return "Error: Firecrawl API key not configured. Set it in Settings > Browser."
        from cptr.utils.browser.firecrawl import scrape

        content = await scrape(url, key, cfg.get("firecrawl_base_url", ""))
        return f"Navigated to {url} (via Firecrawl)\n\n{content}"

    if provider == "browser_use":
        key = cfg.get("browser_use_api_key", "")
        if not key:
            return "Error: Browser-Use API key not configured. Set it in Settings > Browser."
        from cptr.utils.browser.browser_use import browse

        result = await browse(
            f"Navigate to {url} and describe what you see", key, cfg.get("browser_use_base_url", "")
        )
        return f"Navigated to {url} (via Browser-Use)\n\n{result}"

    # Local CDP
    chat_id = __context__.get("chat_id", "default")
    client = await _get_cdp_session(chat_id)
    result = await client.navigate(url)
    return f"Navigated to {url}\nTitle: {result.get('title', '')}"


async def browser_snapshot(*, __context__: dict) -> str:
    """Get the current page content. For local browser, returns an accessibility tree with ref IDs (@e1, @e2, etc.) that can be used with browser_click and browser_type. For cloud providers, returns page content as text."""
    cfg = await _get_browser_config()
    provider = cfg.get("provider", "local")

    if provider in ("firecrawl", "browser_use"):
        return "Snapshot is only meaningful after browser_navigate. The navigate result already contains the page content."

    chat_id = __context__.get("chat_id", "default")
    client = await _get_cdp_session(chat_id)
    return await client.snapshot()


async def browser_click(ref: str, *, __context__: dict) -> str:
    """Click an element on the page identified by its ref ID from the snapshot (e.g. @e1).
    :param ref: The ref ID of the element to click (e.g. @e1, @e5).
    """
    cfg = await _get_browser_config()
    if cfg.get("provider", "local") != "local":
        return "Error: browser_click requires Local CDP provider. Cloud providers (Firecrawl, Browser-Use) don't support interactive browsing. Switch to Local CDP in Settings > Browser."

    chat_id = __context__.get("chat_id", "default")
    client = await _get_cdp_session(chat_id)
    await client.click(ref)
    # Return updated snapshot so the AI sees the result
    return await client.snapshot()


async def browser_type(ref: str, text: str, *, __context__: dict) -> str:
    """Type text into an input element identified by its ref ID from the snapshot.
    :param ref: The ref ID of the input element (e.g. @e3).
    :param text: The text to type.
    """
    cfg = await _get_browser_config()
    if cfg.get("provider", "local") != "local":
        return "Error: browser_type requires Local CDP provider. Switch to Local CDP in Settings > Browser."

    chat_id = __context__.get("chat_id", "default")
    client = await _get_cdp_session(chat_id)
    await client.type_text(ref, text)
    return await client.snapshot()


async def browser_screenshot(
    width: Optional[int] = None, height: Optional[int] = None, *, __context__: dict
) -> str:
    """Take a screenshot of the current browser page. Saves the image to the workspace.
    :param width: Optional screenshot viewport width in CSS pixels.
    :param height: Optional screenshot viewport height in CSS pixels.
    """
    cfg = await _get_browser_config()
    if cfg.get("provider", "local") != "local":
        return "Error: browser_screenshot requires Local CDP provider."
    if (width is None) != (height is None):
        return "Error: browser_screenshot width and height must be provided together."
    if width is not None and height is not None and (width <= 0 or height <= 0):
        return "Error: browser_screenshot width and height must be positive integers."

    chat_id = __context__.get("chat_id", "default")
    client = await _get_cdp_session(chat_id)
    png_bytes = await client.screenshot(width=width, height=height)

    # Save to workspace
    workspace = __context__.get("workspace", ".")

    import time

    filename = f"screenshot_{int(time.time())}.png"
    filepath = Path(workspace) / ".cptr" / "screenshots" / filename
    request = __context__.get("request")
    try:
        if request is None:
            return "Error: request context unavailable"
        await Runtime.write_file(request, str(filepath), png_bytes)
    except FileError as e:
        return f"Error: {e}"

    return f"Screenshot saved: {filepath}"


async def browser_evaluate(javascript: str, *, __context__: dict) -> str:
    """Execute JavaScript in the browser page and return the result.
    :param javascript: The JavaScript expression to evaluate.
    """
    cfg = await _get_browser_config()
    if cfg.get("provider", "local") != "local":
        return "Error: browser_evaluate requires Local CDP provider."

    chat_id = __context__.get("chat_id", "default")
    client = await _get_cdp_session(chat_id)
    return await client.evaluate(javascript)


async def image_generate(
    prompt: str,
    image: Optional[str] = None,
    images: Optional[list[str]] = None,
    size: Optional[str] = None,
    n: int = 1,
    background: Optional[str] = None,
    *,
    __context__: dict,
) -> str:
    """Generate or edit image files from a prompt.
    Returns saved image file paths. You must call display_file next for each returned path
    before responding to the user.
    :param prompt: Detailed description of the image to create or the edits to make.
    :param image: Optional source image file id, /api/files/... URL, or workspace path for edit mode.
    :param images: Optional source image file ids, /api/files/... URLs, or workspace paths for edit mode.
    :param size: Optional image size, such as 1024x1024.
    :param n: Number of images to create, from 1 to 4.
    :param background: Optional background setting supported by the image provider.
    """
    image_refs: list[str] = []
    if image:
        image_refs.append(image)
    if images:
        image_refs.extend(images)

    if image_refs:
        from cptr.utils.images import edit_images

        request = __context__.get("request")
        if request is None:
            return "Error: request context unavailable"
        results = await edit_images(
            request,
            prompt,
            image_refs,
            user_id=__context__.get("user_id"),
            size=size,
            n=n,
            background=background,
            workspace=__context__.get("workspace"),
        )
        kind = "edit"
    else:
        from cptr.utils.images import generate_images

        request = __context__.get("request")
        if request is None:
            return "Error: request context unavailable"
        results = await generate_images(
            request,
            prompt,
            user_id=__context__.get("user_id"),
            size=size,
            n=n,
            workspace=__context__.get("workspace"),
        )
        kind = "generation"

    return json.dumps(
        {
            "status": "success",
            "kind": kind,
            "images": [result.as_dict() for result in results],
        },
        ensure_ascii=False,
    )


async def update_memory(
    scope: Literal["user", "workspace"],
    operations: list[dict],
    *,
    __context__: dict,
) -> str:
    """Save durable memories about the user or current workspace.

    Use user memory for stable preferences, communication style, and cross-workspace
    facts. Use workspace memory for repo-specific conventions, verification
    commands, architecture notes, and local tool quirks. Make all changes in one
    operations array so removals/replacements and additions apply atomically. Simple
    add/replace/remove operations update the baseline USER.md/WORKSPACE.md bullet list.
    Operations with path, heading, memory_id, link, move, split, or merge edit the
    Markdown memory vault.
    :param scope: "user" for global per-user memory, or "workspace" for the current workspace only.
    :param operations: Batch of memory operations. Supported actions are add, replace, remove, link, move, split, and merge.
    """
    from cptr.utils.memory import get_memory_settings, remember

    user_id = __context__.get("user_id")
    workspace = __context__.get("workspace", "")
    if not user_id:
        return json.dumps({"success": False, "error": "user_id missing from tool context"})
    if not isinstance(operations, list):
        return json.dumps({"success": False, "error": "operations must be a list"})
    request = __context__.get("request")
    if request is None:
        return json.dumps({"success": False, "error": "request context unavailable"})
    settings = await get_memory_settings()
    if not settings.get("tool_enabled", True):
        return json.dumps({"success": False, "error": "memory tool is disabled"})

    result = await remember(
        request,
        user_id=user_id,
        workspace=workspace,
        scope=scope,
        operations=operations,
    )
    return json.dumps(result, ensure_ascii=False)


def _shape_chat_search_result(row: dict) -> dict:
    meta = row.get("meta") or {}
    return {
        "chat_id": row.get("id"),
        "title": row.get("title"),
        "workspace": meta.get("workspace", ""),
        "updated_at": row.get("updated_at"),
        "created_at": row.get("created_at"),
        "match_type": row.get("match_type"),
        "snippet": row.get("snippet"),
        "matched_message_id": row.get("matched_message_id"),
        "matched_role": row.get("matched_role"),
    }


def _shape_chat_tool_message(message) -> dict:
    payload = {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
    }
    if message.model:
        payload["model"] = message.model
    if message.meta:
        payload["meta"] = message.meta
    return payload


async def search_chats(
    query: str = "",
    chat_id: str = "",
    around_message_id: str = "",
    window: int = 5,
    limit: int = 5,
    workspace_scope: Literal["current", "all"] = "current",
    include_subagents: bool = False,
    *,
    __context__: dict,
) -> str:
    """Search or read prior chats from cptr's existing chat history.

    With no args, browse recent chats. Pass query to search previous chats.
    Pass chat_id to read a bounded transcript. Pass chat_id plus around_message_id
    to read a window around a specific message.
    :param query: Text to search in chat ids, titles, summaries, and message content.
    :param chat_id: Chat id to read directly.
    :param around_message_id: Message id to center a window on when chat_id is set.
    :param window: Number of messages before and after around_message_id, from 1 to 20.
    :param limit: Maximum chats to return for browse/search, from 1 to 10.
    :param workspace_scope: "current" searches only this workspace; "all" searches every workspace owned by the user.
    :param include_subagents: Include delegated sub-agent chats in browse/search/read results.
    """
    from sqlalchemy import select

    from cptr.models import Chat, ChatMessage, is_internal_chat
    from cptr.utils.db import get_db

    user_id = __context__.get("user_id")
    current_chat_id = __context__.get("chat_id")
    current_workspace = __context__.get("workspace", "")
    if not user_id:
        return json.dumps({"success": False, "error": "user_id missing from tool context"})

    try:
        limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        limit = 5
    try:
        window = max(1, min(int(window), 20))
    except (TypeError, ValueError):
        window = 5

    workspace = current_workspace if workspace_scope == "current" else None

    async def get_allowed_chat(cid: str):
        chat = await Chat.get_by_id(cid)
        if not chat or chat.user_id != user_id:
            return None, "chat not found"
        meta = chat.meta or {}
        if not include_subagents and is_internal_chat(meta):
            return None, "chat is an internal chat"
        if workspace and meta.get("workspace") != workspace:
            return None, "chat is outside the current workspace"
        return chat, None

    if chat_id and around_message_id:
        chat, error = await get_allowed_chat(chat_id)
        if error:
            return json.dumps({"success": False, "error": error, "chat_id": chat_id})
        messages = await ChatMessage.get_all_by_chat(chat_id)
        anchor_index = next(
            (idx for idx, message in enumerate(messages) if message.id == around_message_id),
            -1,
        )
        if anchor_index < 0:
            return json.dumps(
                {
                    "success": False,
                    "error": "around_message_id not found in chat",
                    "chat_id": chat_id,
                    "around_message_id": around_message_id,
                }
            )
        start = max(0, anchor_index - window)
        end = min(len(messages), anchor_index + window + 1)
        return json.dumps(
            {
                "success": True,
                "mode": "window",
                "chat_id": chat_id,
                "title": chat.title,
                "around_message_id": around_message_id,
                "messages_before": start,
                "messages_after": len(messages) - end,
                "messages": [_shape_chat_tool_message(message) for message in messages[start:end]],
            },
            ensure_ascii=False,
        )

    if chat_id:
        chat, error = await get_allowed_chat(chat_id)
        if error:
            return json.dumps({"success": False, "error": error, "chat_id": chat_id})
        messages = await ChatMessage.get_all_by_chat(chat_id)
        head = 20
        tail = 10
        truncated = len(messages) > head + tail
        visible = messages[:head] + messages[-tail:] if truncated else messages
        return json.dumps(
            {
                "success": True,
                "mode": "read",
                "chat_id": chat_id,
                "title": chat.title,
                "workspace": (chat.meta or {}).get("workspace", ""),
                "message_count": len(messages),
                "truncated": truncated,
                "messages": [_shape_chat_tool_message(message) for message in visible],
                "hint": (
                    "Pass chat_id plus around_message_id from one of these messages to inspect the middle."
                    if truncated
                    else None
                ),
            },
            ensure_ascii=False,
        )

    if query.strip():
        rows = await Chat.search_by_text(
            user_id=user_id,
            query=query,
            limit=limit + 1,
            workspace=workspace,
            include_subagents=include_subagents,
        )
        results = [
            _shape_chat_search_result(row) for row in rows if row.get("id") != current_chat_id
        ][:limit]
        return json.dumps(
            {
                "success": True,
                "mode": "search",
                "query": query,
                "workspace_scope": workspace_scope,
                "results": results,
                "count": len(results),
            },
            ensure_ascii=False,
        )

    async with await get_db() as db:
        result = await db.execute(
            select(Chat).where(Chat.user_id == user_id).order_by(Chat.updated_at.desc())
        )
        chats = list(result.scalars().all())

    recent = []
    for chat in chats:
        meta = chat.meta or {}
        if chat.id == current_chat_id:
            continue
        if not include_subagents and is_internal_chat(meta):
            continue
        if workspace and meta.get("workspace") != workspace:
            continue
        recent.append(
            {
                "chat_id": chat.id,
                "title": chat.title,
                "workspace": meta.get("workspace", ""),
                "updated_at": chat.updated_at,
                "created_at": chat.created_at,
                "summary": chat.summary,
            }
        )
        if len(recent) >= limit:
            break

    return json.dumps(
        {
            "success": True,
            "mode": "browse",
            "workspace_scope": workspace_scope,
            "results": recent,
            "count": len(recent),
        },
        ensure_ascii=False,
    )


async def notify(message: str, target: str = "", title: str = "", *, __context__: dict) -> str:
    """Send a notification to a user notification target.

    :param message: Message body to send.
    :param target: Optional notification target ID from Settings > Notifications. Uses the default target when omitted.
    :param title: Optional notification title.
    """
    user_id = __context__.get("user_id")
    if not user_id:
        return "Error: authentication required."
    try:
        from cptr.utils.notifications import NotificationError, notify_target

        return await notify_target(user_id, message, target or None, title or None)
    except NotificationError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: failed to send notification: {exc}"


async def ui_metrics(
    metric: str = "dwell",
    window_hours: int = 24,
    cap_s: int = 120,
    kind: Optional[str] = None,
    limit: int = 15,
    *,
    __context__: dict | None = None,
) -> str:
    """Summarise the client UI performance samples cptr has collected.

    Reads the `ui_events` table for THIS instance's database, so the numbers
    always describe the lane you are actually running in. Read-only.

    metric:
      - "dwell": active time per workspace. Uses measured `dwell` samples when
        the frontend reports them, otherwise falls back to attributing gaps
        between interaction samples (an estimate — see the caveat it prints).
      - "slow": slowest interactions as p50/p95/max per (kind, label).
      - "inventory": how much data exists — kinds, sessions, coverage.

    Args:
        metric: One of "dwell", "slow", "inventory".
        window_hours: How far back to look, by event time.
        cap_s: For the "dwell" fallback, the longest gap still counted as time
            spent (guards against idle periods inflating a workspace).
        kind: Restrict "slow" to a single event kind, e.g. "mount".
        limit: Maximum rows to list.
    """
    from collections import defaultdict
    import datetime as dt

    from cptr.models.ui_events import UiEvent, _percentile
    from cptr.utils.config import now_ms

    try:
        window_hours = max(1, min(int(window_hours or 24), 24 * 90))
        limit = max(1, min(int(limit or 15), 100))
    except (TypeError, ValueError):
        return "Error: window_hours and limit must be integers."

    since = now_ms() - window_hours * 3600 * 1000
    rows = await UiEvent.scan(since)
    if not rows:
        return (
            f"No UI performance samples in the last {window_hours}h. The collector "
            "posts to /api/ui-events; check it isn't switched off."
        )

    def stamp(ms: float) -> str:
        return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")

    if metric == "inventory":
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            counts[row["kind"]] += 1
        sessions = {row["session_id"] for row in rows if row["session_id"]}
        lines = [
            f"ui_events — last {window_hours}h",
            f"  {len(rows)} samples across {len(sessions)} browser session(s)",
            f"  span: {stamp(rows[0]['ts'])} -> {stamp(rows[-1]['ts'])}",
            "",
            "  samples by kind:",
        ]
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {count:7d}  {name}")
        return "\n".join(lines)

    if metric == "slow":
        groups: dict[tuple[str, str | None], list[float]] = defaultdict(list)
        for row in rows:
            if kind and row["kind"] != kind:
                continue
            groups[(row["kind"], row["label"])].append(float(row["duration_ms"] or 0.0))
        if not groups:
            return f"No samples for kind={kind!r} in the last {window_hours}h."
        for durations in groups.values():
            durations.sort()
        ranked = sorted(groups.items(), key=lambda kv: -_percentile(kv[1], 95))
        lines = [
            f"Slowest UI interactions — last {window_hours}h (ms, slowest p95 first)",
            "",
            f"  {'p95':>8} {'p50':>8} {'max':>9} {'n':>6}  kind / label",
        ]
        for (group_kind, label), durations in ranked[:limit]:
            lines.append(
                f"  {_percentile(durations, 95):8.1f} {_percentile(durations, 50):8.1f} "
                f"{durations[-1]:9.1f} {len(durations):6d}  {group_kind} / {label or '-'}"
            )
        return "\n".join(lines)

    if metric != "dwell":
        return f"Unknown metric {metric!r}. Use one of: dwell, slow, inventory."

    per_workspace: dict[str, float] = defaultdict(float)
    measured = [row for row in rows if row["kind"] == "dwell" and (row["duration_ms"] or 0) > 0]
    if measured:
        for row in measured:
            per_workspace[row["workspace"] or "(unknown)"] += float(row["duration_ms"]) / 1000.0
        method = f"measured ({len(measured)} dwell samples)"
        caveat = "  Counts only while the tab is visible; " \
                 "in-flight time in the current workspace is not yet flushed."
    else:
        cap = max(10, min(int(cap_s or 120), 3600))
        for earlier, later in zip(rows, rows[1:]):
            gap = (later["ts"] - earlier["ts"]) / 1000.0
            if 0 < gap <= cap:
                per_workspace[earlier["workspace"] or "(unknown)"] += gap
        method = f"ESTIMATED from inter-event gaps (cap {cap}s) — no dwell samples yet"
        caveat = (
            "  Estimate: each gap is attributed to the earlier event's workspace, so time\n"
            "  after switching away is misattributed. Compare workspaces, don't trust the\n"
            "  absolute minutes — and treat long gaps as unattributable."
        )

    total = sum(per_workspace.values())
    if total <= 0:
        return f"No attributable time in the last {window_hours}h."

    lines = [f"Time per workspace — last {window_hours}h, {method}", ""]
    for workspace, seconds in sorted(per_workspace.items(), key=lambda kv: -kv[1])[:limit]:
        share = 100.0 * seconds / total
        lines.append(f"  {share:5.1f}%  {seconds / 60:8.1f} min  {workspace}")
    lines.append("")
    lines.append(f"  total tracked: {total / 60:.0f} min across {len(per_workspace)} workspace(s)")
    lines.append(caveat)
    return "\n".join(lines)


_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_LOCAL_HOST_RE = re.compile(
    r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?([/?#]|$)", re.IGNORECASE
)


def _qualify_url(raw: str) -> str:
    """Turn ``8765``, ``localhost:8765/x`` or ``example.com`` into a URL."""
    value = (raw or "").strip().strip("<>\"'")
    if not value:
        return ""
    if value.isdigit():
        return f"http://localhost:{value}/"
    if not _URL_SCHEME_RE.match(value):
        value = f"http://{value}"
    return value


def _browser_label(url: str) -> str:
    """Short tab label (``host:port``) when the model doesn't name one."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return "Browser"
    if not host:
        return "Browser"
    return f"{host}:{port}" if port else host


async def open_browser(
    url: str, where: str = "auto", label: str = "", *, __context__: dict
) -> str:
    """Open a web page in the browser the user is looking at, so they see it immediately.

    Use this to *show* something: right after building a web app, starting a dev
    server or static file server, or whenever the user asks to see/open a page.
    Do not make the user copy a URL or click anything first -- that is the whole
    point. The browser_* tools are the opposite: they drive a hidden automation
    browser that only you can inspect, so they never show the user anything.
    :param url: http(s) URL, or shorthand: "8765" / "localhost:8765" / "example.com".
    :param where: "auto" (default) opens a Browser tab inside the cptr UI when
        there is a workspace and a connected app, and otherwise the user's own
        desktop browser. "app" forces the in-app tab, "system" forces the desktop
        browser, "both" does both.
    :param label: Optional tab label, e.g. "Todo demo".
    """
    user_id = __context__.get("user_id")
    if not user_id:
        return "Error: authentication required."

    target = (where or "auto").strip().lower()
    if target in ("browser", "tab", "in-app", "inapp"):
        target = "app"
    elif target in ("desktop", "os", "default"):
        target = "system"
    elif target == "":
        target = "auto"
    if target not in ("auto", "app", "system", "both"):
        return "Error: where must be 'auto', 'app', 'system' or 'both'."

    qualified = _qualify_url(url)
    if not qualified:
        return "Error: url is required."
    parsed = urlsplit(qualified)
    if parsed.scheme not in ("http", "https", "file"):
        return f"Error: unsupported URL scheme: {parsed.scheme}"
    if parsed.scheme != "file" and not parsed.netloc:
        return f"Error: not a valid URL: {url}"

    notes: list[str] = []
    if parsed.scheme == "file" and target in ("auto", "app", "both"):
        # The in-app Browser tab proxies over http(s) only.
        notes.append("file:// URLs can only open in the system browser")
        target = "system"

    from cptr.socket.main import emit_open_browser, is_user_active

    workspace = str(__context__.get("workspace") or "")
    chat_id = str(__context__.get("chat_id") or "")
    tab_label = label.strip() or _browser_label(qualified)
    app_possible = bool(workspace) and is_user_active(user_id)
    want_app = target in ("auto", "app", "both") and app_possible
    want_system = target in ("system", "both") or (target == "auto" and not app_possible)
    opened: dict[str, bool] = {}

    if want_app:
        await emit_open_browser(
            user_id, qualified, chat_id=chat_id, workspace=workspace, label=tab_label
        )
        opened["app"] = True
    elif target in ("app", "both"):
        notes.append(
            "no cptr app is connected, so no in-app Browser tab"
            if workspace
            else "no workspace is open, so no in-app Browser tab"
        )

    if want_system:
        from cptr.utils.browser.opener import open_in_system_browser

        launched, detail = await open_in_system_browser(qualified)
        opened["system"] = launched
        if not launched:
            notes.append(f"system browser did not open: {detail}")

    if not any(opened.values()):
        return json.dumps(
            {
                "success": False,
                "url": qualified,
                "opened": opened,
                "error": "; ".join(notes) or "nothing was opened",
                "hint": "Retry with where='system' to open the user's desktop browser.",
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "success": True,
            "url": qualified,
            "label": tab_label,
            "opened": opened,
            "notes": notes,
        },
        ensure_ascii=False,
    )


# ── Registry ────────────────────────────────────────────────

ToolApprovalPolicy = Literal["allow", "review"]
TOOL_APPROVAL_POLICIES = {"allow", "review"}


def normalize_tool_approval(value: Any) -> ToolApprovalPolicy | None:
    return value if isinstance(value, str) and value in TOOL_APPROVAL_POLICIES else None


TOOLS: dict[str, dict] = {
    # Auto mode runs these without asking.
    "read_file": {"fn": read_file, "approval": "allow"},
    "list_directory": {"fn": list_directory, "approval": "allow"},
    "search_files": {"fn": search_files, "approval": "allow"},
    "git_status": {"fn": git_status, "approval": "allow"},
    "git_log": {"fn": git_log, "approval": "allow"},
    "git_show": {"fn": git_show, "approval": "allow"},
    "git_diff": {"fn": git_diff, "approval": "allow"},
    "git_blame": {"fn": git_blame, "approval": "allow"},
    "check_task": {"fn": check_task, "approval": "allow"},
    "web_search": {"fn": web_search, "approval": "allow"},
    "read_url": {"fn": read_url, "approval": "allow"},
    "search_chats": {"fn": search_chats, "approval": "allow"},
    "create_download_link": {"fn": create_download_link, "approval": "allow"},
    # Puts a page in front of the user (in-app Browser tab and/or their desktop
    # browser). Approval-free: showing a page is the whole point of the tool.
    "open_browser": {"fn": open_browser, "approval": "allow"},
    "list_automations": {"fn": list_automations, "approval": "allow"},
    "list_workspace_todos": {"fn": list_workspace_todos, "approval": "allow"},
    "view_skill": {"fn": view_skill, "approval": "allow"},
    "update_tasks": {"fn": update_tasks, "approval": "allow"},
    # Missing approval inherits tool_approval.default_builtin_approval.
    "create_file": {"fn": create_file},
    "display_file": {"fn": display_file},
    "edit_file": {"fn": edit_file},
    "multi_edit_file": {"fn": multi_edit_file},
    "write_file": {"fn": write_file},
    "run_command": {"fn": run_command},
    "send_input": {"fn": send_input},
    "kill_task": {"fn": kill_task},
    "create_automation": {"fn": create_automation},
    "update_automation": {"fn": update_automation},
    "toggle_automation": {"fn": toggle_automation},
    "delete_automation": {"fn": delete_automation},
    # Todo mutations only enqueue a human-verification request (never mutate).
    "add_workspace_todo": {"fn": add_workspace_todo, "approval": "allow"},
    "complete_workspace_todo": {"fn": complete_workspace_todo, "approval": "allow"},
    "reopen_workspace_todo": {"fn": reopen_workspace_todo, "approval": "allow"},
    "remove_workspace_todo": {"fn": remove_workspace_todo, "approval": "allow"},
    "notify": {"fn": notify},
    "image_generate": {"fn": image_generate},
    "manage_skill": {"fn": manage_skill},
    "update_memory": {"fn": update_memory, "approval": "allow"},
    # Read-only introspection of our own telemetry; needs no workspace.
    "ui_metrics": {"fn": ui_metrics, "approval": "allow"},
}

# Browser tools — conditionally included in schemas based on browser.enabled
BROWSER_TOOLS: dict[str, dict] = {
    "browser_navigate": {"fn": browser_navigate},
    "browser_snapshot": {"fn": browser_snapshot, "approval": "allow"},
    "browser_click": {"fn": browser_click},
    "browser_type": {"fn": browser_type},
    "browser_screenshot": {"fn": browser_screenshot, "approval": "allow"},
    "browser_evaluate": {"fn": browser_evaluate},
}


# ── Sub-agent ───────────────────────────────────────────────

_DEFAULT_SUBAGENT_SYSTEM = """You are a sub-agent working on a specific task assigned by the lead agent.

You have full access to the workspace — you can read, write, edit files, and run commands.
Focus exclusively on your assigned task. Do NOT work on anything outside your scope.

When done, end with a clear summary:
- What you did
- What files you changed (if any)
- Any issues or open questions
"""

_subagent_semaphore: asyncio.Semaphore | None = None


async def _get_subagent_config() -> dict:
    """Load sub-agent settings from config with defaults."""
    from cptr.models import Config

    return {
        "max_concurrent": int(await Config.get("subagents.max_concurrent") or 20),
        "background_enabled": (await Config.get("subagents.background_enabled"))
        in (True, "true", "1"),
        "max_async": int(await Config.get("subagents.max_async") or 20),
        "max_iterations": int(await Config.get("subagents.max_iterations") or 30),
        "max_output": int(await Config.get("subagents.max_output") or 30_000),
        "system_prompt": (await Config.get("subagents.system_prompt")) or _DEFAULT_SUBAGENT_SYSTEM,
    }


def _truncate_output(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending a note if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[output truncated]"


async def delegate_task(
    task: str,
    context: str = "",
    background: bool = False,
    *,
    __context__: dict,
) -> str:
    """Delegate a task to a sub-agent. Use background=true for long-running independent work.

    :param task: What the sub-agent should do.
    :param context: Optional context (e.g. relevant file paths, decisions made so far).
    :param background: Return a handle immediately and inject the final result into this chat when done.
    """
    global _subagent_semaphore
    config = await _get_subagent_config()

    if background:
        if not config["background_enabled"]:
            return "Error: background sub-agents are disabled in settings."

        from cptr.utils.async_subagents import (
            attach_subagent_chat,
            fail_reserved_subagent,
            reserve_async_subagent,
            start_async_subagent,
        )

        reserve = await reserve_async_subagent(
            config["max_async"],
            request=__context__["request"],
            task=task,
            context=context,
            workspace=__context__["workspace"],
            user_id=__context__["user_id"],
            parent_chat_id=__context__["chat_id"],
            parent_message_id=__context__.get("message_id"),
            connection=__context__["connection"],
            model=__context__["model_id"],
            model_id=__context__.get("full_model_id") or __context__["model_id"],
        )
        if reserve.get("status") == "rejected":
            return f"Error: {reserve['error']}"

        delegation_id = reserve["delegation_id"]
        try:
            chat, _, assistant_msg = await _create_subagent_chat(
                __context__["request"],
                task=task,
                context=context,
                workspace=__context__["workspace"],
                model=__context__["model_id"],
                user_id=__context__["user_id"],
                parent_chat_id=__context__["chat_id"],
                delegation_id=delegation_id,
            )
        except Exception as e:
            await fail_reserved_subagent(delegation_id, str(e))
            return f"Error: failed to create background sub-agent: {e}"

        await attach_subagent_chat(
            delegation_id,
            subagent_chat_id=chat.id,
            subagent_message_id=assistant_msg.id,
        )

        async def _runner() -> str:
            return await _run_existing_subagent_chat(
                assistant_msg_id=assistant_msg.id,
                chat_id=chat.id,
                workspace=__context__["workspace"],
                connection=__context__["connection"],
                model=__context__["model_id"],
                user_id=__context__["user_id"],
                config=config,
            )

        await start_async_subagent(delegation_id, _runner)
        return json.dumps(
            {
                "status": "dispatched",
                "delegation_id": delegation_id,
                "subagent_chat_id": chat.id,
                "mode": "background",
                "task": task,
            }
        )

    if config["max_concurrent"] == -1:
        return await _run_subagent_chat(
            __context__["request"],
            task=task,
            context=context,
            workspace=__context__["workspace"],
            connection=__context__["connection"],
            model=__context__["model_id"],
            user_id=__context__["user_id"],
            parent_chat_id=__context__["chat_id"],
            config=config,
        )

    if _subagent_semaphore is None:
        _subagent_semaphore = asyncio.Semaphore(max(1, config["max_concurrent"]))

    async with _subagent_semaphore:
        return await _run_subagent_chat(
            __context__["request"],
            task=task,
            context=context,
            workspace=__context__["workspace"],
            connection=__context__["connection"],
            model=__context__["model_id"],
            user_id=__context__["user_id"],
            parent_chat_id=__context__["chat_id"],
            config=config,
        )


async def timer(
    prompt: str,
    at: str,
    cancel_on: list[Literal["chat.read", "chat.user_message"]] | None = None,
    *,
    __context__: dict,
) -> str:
    """Set a one-shot timer for this chat.

    Use it when time is the missing input: wait for a deployment, retry after
    backoff, revisit work after a deadline, or follow up after silence. When
    it fires, the agent receives `prompt` and the latest thread context; it may
    act, set another timer, or finish silently.

    `at` is normally relative to now. Prefer `10s`, `5m`, `1h`, or `2d`; plain
    language such as `in 10 seconds` also works. Use an RFC 3339 timestamp
    with a timezone only for a real calendar deadline.

    `cancel_on` prevents launch when `chat.read` or `chat.user_message`
    happens first in this chat. Omit it when timed work must run regardless.
    """
    from cptr.utils.timers import parse_timer_at

    if not prompt.strip():
        return "Error: prompt must not be empty."

    try:
        due_at = parse_timer_at(at)
    except ValueError as exc:
        return f"Error: {exc}"

    selected_events = cancel_on or []
    allowed_events = {"chat.read", "chat.user_message"}
    if any(event not in allowed_events for event in selected_events):
        return "Error: cancel_on accepts only chat.read and chat.user_message."
    selected_events = list(dict.fromkeys(selected_events))

    full_model_id = __context__.get("full_model_id") or __context__["model_id"]
    chat, _, _ = await _create_subagent_chat(
        __context__["request"],
        task=prompt,
        context="",
        workspace=__context__["workspace"],
        model=full_model_id,
        user_id=__context__["user_id"],
        parent_chat_id=__context__["chat_id"],
        child_type="timer",
        deferred=True,
        extra_meta={
            "timer_at": due_at,
            "cancel_on": selected_events,
            "status": "pending",
            "timer_parent_message_id": __context__.get("message_id"),
            "timer_model_id": full_model_id,
        },
    )

    from datetime import datetime, timezone

    return json.dumps(
        {
            "status": "set",
            "at": datetime.fromtimestamp(due_at / 1_000_000_000, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "cancel_on": selected_events,
        }
    )


async def _create_subagent_chat(
    request: Request,
    task: str,
    context: str,
    workspace: str,
    model: str,
    user_id: str,
    parent_chat_id: str,
    delegation_id: str | None = None,
    *,
    child_type: str = "subagent",
    deferred: bool = False,
    extra_meta: dict | None = None,
):
    """Create the real chat/messages used by a sub-agent."""
    from cptr.models import Chat, ChatMessage
    from cptr.utils.chat_export import export_chat_to_file
    from cptr.utils.config import now_ms

    user_content = f"{task}\n\n## Context\n{context}" if context else task
    meta = {
        "workspace": workspace,
        "internal": True,
        "type": child_type,
        "parent_chat_id": parent_chat_id,
        "params": {
            "tool_approval_mode": "full",  # auto-approve all tools
        },
    }
    if delegation_id:
        meta["delegation_id"] = delegation_id
    if extra_meta:
        meta.update(extra_meta)

    chat = await Chat.create(
        user_id=user_id,
        title=f"{child_type.title()}: {task[:60]}",
        meta=meta,
        created_at=now_ms(),
    )

    user_msg = await ChatMessage.create(
        chat_id=chat.id,
        role="user",
        content=user_content,
        created_at=now_ms(),
    )

    assistant_msg = None
    if deferred:
        await Chat.update_current_message(chat.id, user_msg.id, now_ms())
    else:
        assistant_msg = await ChatMessage.create(
            chat_id=chat.id,
            role="assistant",
            content="",
            parent_id=user_msg.id,
            model=model,
            done=False,
            created_at=now_ms(),
        )
        await Chat.update_current_message(chat.id, assistant_msg.id, now_ms())
    await export_chat_to_file(request, chat.id)
    return chat, user_msg, assistant_msg


async def _run_existing_subagent_chat(
    assistant_msg_id: str,
    chat_id: str,
    workspace: str,
    connection: dict,
    model: str,
    user_id: str,
    config: dict,
) -> str:
    """Run the agent loop for an already-created sub-agent chat."""
    from cptr.models import ChatMessage
    from cptr.utils.chat_task import run_chat_task
    from cptr.utils.model_targets import ApiModelTarget

    await run_chat_task(
        None,
        message_id=assistant_msg_id,
        chat_id=chat_id,
        user_id=user_id,
        target=ApiModelTarget(
            kind="api",
            connection=connection,
            runtime_model=model,
            full_model_id=model,
        ),
        workspace=workspace,
    )

    result_msg = await ChatMessage.get_by_id(assistant_msg_id)
    output = result_msg.content if result_msg else "Sub-agent produced no output."

    return _truncate_output(output, config["max_output"])


async def _run_subagent_chat(
    request: Request,
    task: str,
    context: str,
    workspace: str,
    connection: dict,
    model: str,
    user_id: str,
    parent_chat_id: str,
    config: dict,
) -> str:
    """Create a real chat and run the agent loop on it."""
    chat, _, assistant_msg = await _create_subagent_chat(
        request,
        task=task,
        context=context,
        workspace=workspace,
        model=model,
        user_id=user_id,
        parent_chat_id=parent_chat_id,
    )
    return await _run_existing_subagent_chat(
        assistant_msg_id=assistant_msg.id,
        chat_id=chat.id,
        workspace=workspace,
        connection=connection,
        model=model,
        user_id=user_id,
        config=config,
    )


SUBAGENT_TOOLS: dict[str, dict] = {
    "delegate_task": {"fn": delegate_task, "approval": "allow"},
    "timer": {"fn": timer},
}

# Combined lookup for execution and approval (always available regardless of config)
ALL_TOOLS: dict[str, dict] = {**TOOLS, **BROWSER_TOOLS, **SUBAGENT_TOOLS}


async def resolve_builtin_tool_approval(name: str) -> ToolApprovalPolicy:
    """Resolve built-in tool approval. Unknown tools stay conservative."""
    tool = ALL_TOOLS.get(name)
    if tool is None:
        return "review"

    from cptr.models import Config

    overrides = await Config.get("tool_approval.builtin_tools") or {}
    if isinstance(overrides, dict):
        override = normalize_tool_approval(overrides.get(name))
        if override:
            return override

    registry = normalize_tool_approval(tool.get("approval"))
    if registry:
        return registry

    default = normalize_tool_approval(await Config.get("tool_approval.default_builtin_approval"))
    return default or "review"


BUILTIN_TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "files": (
        "read_file",
        "list_directory",
        "search_files",
        "create_file",
        "display_file",
        "create_download_link",
        "edit_file",
        "multi_edit_file",
        "write_file",
    ),
    "terminal": ("run_command", "send_input", "check_task", "kill_task"),
    "git": ("git_status", "git_log", "git_show", "git_diff", "git_blame"),
    "web": ("web_search", "read_url"),
    "browser": (
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_screenshot",
        "browser_evaluate",
        "open_browser",
    ),
    "memory": ("update_memory",),
    "chats": ("search_chats",),
    "skills": ("view_skill", "manage_skill"),
    "tasks": ("update_tasks",),
    "automations": (
        "list_automations",
        "create_automation",
        "update_automation",
        "toggle_automation",
        "delete_automation",
    ),
    "todos": (
        "list_workspace_todos",
        "add_workspace_todo",
        "complete_workspace_todo",
        "reopen_workspace_todo",
        "remove_workspace_todo",
    ),
    "images": ("image_generate",),
    "subagents": ("delegate_task",),
    "notifications": ("notify",),
    "telemetry": ("ui_metrics",),
}

GLOBAL_CHAT_DISABLED_TOOLS = {
    *BUILTIN_TOOL_GROUPS["files"],
    *BUILTIN_TOOL_GROUPS["terminal"],
    *BUILTIN_TOOL_GROUPS["browser"],
    *BUILTIN_TOOL_GROUPS["git"],
    *BUILTIN_TOOL_GROUPS["automations"],
    *BUILTIN_TOOL_GROUPS["images"],
    "manage_skill",
}

# Showing the user a page needs no workspace -- a workspace-free chat can still
# open the desktop browser (it just has no tab strip to put a Browser tab in).
GLOBAL_CHAT_DISABLED_TOOLS.discard("open_browser")


def disabled_builtin_tool_names(builtin_tools: dict | None) -> set[str]:
    """Return builtin tool names disabled by group config."""
    if not isinstance(builtin_tools, dict):
        return set()
    disabled: set[str] = set()
    for group, enabled in builtin_tools.items():
        if enabled is False:
            disabled.update(BUILTIN_TOOL_GROUPS.get(group, ()))
    return disabled


def is_builtin_tool_enabled(name: str, builtin_tools: dict | None) -> bool:
    return name not in disabled_builtin_tool_names(builtin_tools)


# ── External tool servers ───────────────────────────────────

_tool_server_cache: dict | None = None  # {"servers": [...], "tools": {name: {server, spec}}}


def server_allowed_in_workspace(server: dict, attached: list[str] | None) -> bool:
    """Whether a registered tool server is visible in a workspace.

    ``scope=global`` (default, including servers created before this field
    existed) is available in every workspace. ``scope=workspace`` is only
    available when the workspace's ``toolServers`` list contains its id.
    ``attached`` is None when the workspace has never set that list.
    """
    if not server.get("enabled", True):
        return False
    scope = str(server.get("scope") or "global").strip().lower()
    if scope != "workspace":
        return True
    if not attached:
        return False
    return server.get("id") in attached


async def _load_tool_servers() -> dict:
    """Load and cache external tool server config + specs.

    Returns a dict with 'servers' (raw config list) and 'tools' mapping
    prefixed tool names to {server, spec, type}.
    """
    global _tool_server_cache
    if _tool_server_cache is not None:
        return _tool_server_cache

    from cptr.models import Config

    servers = await Config.get("tool_servers") or []
    tools: dict[str, dict] = {}

    for server in servers:
        if not server.get("enabled", True):
            continue

        server_id = server.get("id", "")
        server_type = server.get("type", "openapi")

        try:
            if server_type == "openapi":
                from cptr.utils.openapi import fetch_openapi_spec, convert_openapi_to_tool_specs

                url = server.get("url", "").rstrip("/")
                path = server.get("path", "openapi.json")
                if path.startswith("http"):
                    spec_url = path
                else:
                    spec_url = f"{url}/{path.lstrip('/')}"

                headers = _build_server_headers(server)
                openapi_spec = await fetch_openapi_spec(spec_url, headers)
                server["_openapi_spec"] = openapi_spec

                for spec in convert_openapi_to_tool_specs(openapi_spec):
                    prefixed = f"{server_id}_{spec['name']}"
                    tools[prefixed] = {
                        "server": server,
                        "spec": {**spec, "name": prefixed},
                        "original_name": spec["name"],
                        "type": "openapi",
                    }

            elif server_type == "mcp":
                from cptr.utils.mcp.client import MCPClient

                client = MCPClient()
                headers = _build_server_headers(server)
                await client.connect(server.get("url", ""), headers)

                for spec in await client.list_tool_specs():
                    prefixed = f"{server_id}_{spec['name']}"
                    tools[prefixed] = {
                        "server": server,
                        "spec": {**spec, "name": prefixed},
                        "original_name": spec["name"],
                        "type": "mcp",
                    }

                await client.disconnect()

            elif server_type == "mcp_stdio":
                from cptr.utils.mcp.stdio_manager import stdio_manager

                command = server.get("command", "")
                args = server.get("args", [])
                env = server.get("env")
                cwd = server.get("cwd")

                if not command:
                    continue

                client = await stdio_manager.get_client(server_id, command, args, env, cwd)
                for spec in await client.list_tool_specs():
                    prefixed = f"{server_id}_{spec['name']}"
                    tools[prefixed] = {
                        "server": server,
                        "spec": {**spec, "name": prefixed},
                        "original_name": spec["name"],
                        "type": "mcp_stdio",
                    }
                # Don't disconnect — keep process alive

        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to load tool server '%s'", server_id, exc_info=True
            )

    _tool_server_cache = {"servers": servers, "tools": tools}
    return _tool_server_cache


def invalidate_tool_server_cache() -> None:
    """Clear the external tool server cache, forcing a reload on next access.

    Also disconnects all stdio MCP processes so they can be re-spawned
    with potentially updated configuration.
    """
    global _tool_server_cache
    _tool_server_cache = None
    # Disconnect stdio servers that may have been reconfigured/removed
    try:
        from cptr.utils.mcp.stdio_manager import stdio_manager
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(stdio_manager.disconnect_all())
        else:
            loop.run_until_complete(stdio_manager.disconnect_all())
    except Exception:
        pass


def _build_server_headers(server: dict) -> dict | None:
    """Build auth + custom headers for a tool server connection."""
    headers = dict(server.get("headers") or {})
    auth_type = server.get("auth_type", "bearer")
    if auth_type == "bearer":
        key = server.get("key", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
    return headers or None


def _extract_mcp_result(result: list) -> str:
    """Extract text from MCP tool result content items."""
    texts = []
    for item in result:
        if isinstance(item, dict):
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
            else:
                texts.append(json.dumps(item))
    return "\n".join(texts) if texts else "(no output)"


async def _execute_external_tool(name: str, args: dict) -> str:
    """Execute an external tool by its prefixed name ({server_id}_{tool_name})."""
    cache = await _load_tool_servers()
    tool_info = cache["tools"].get(name)
    if not tool_info:
        return f"Error: external tool '{name}' not found"

    server = tool_info["server"]
    original_name = tool_info["original_name"]
    tool_type = tool_info["type"]
    headers = _build_server_headers(server)

    try:
        if tool_type == "mcp":
            from cptr.utils.mcp.client import MCPClient

            client = MCPClient()
            await client.connect(server.get("url", ""), headers)
            try:
                result = await client.call_tool(original_name, args)
                return _extract_mcp_result(result)
            finally:
                await client.disconnect()

        elif tool_type == "mcp_stdio":
            from cptr.utils.mcp.stdio_manager import stdio_manager

            client = await stdio_manager.get_client(
                server.get("id", ""),
                server.get("command", ""),
                server.get("args", []),
                server.get("env"),
                server.get("cwd"),
            )
            result = await client.call_tool(original_name, args)
            return _extract_mcp_result(result)

        elif tool_type == "openapi":
            from cptr.utils.openapi import execute_openapi_tool

            openapi_spec = server.get("_openapi_spec", {})
            return await execute_openapi_tool(
                server_url=server.get("url", "").rstrip("/"),
                openapi_spec=openapi_spec,
                tool_name=original_name,
                args=args,
                headers=headers,
            )

        else:
            return f"Error: unknown tool server type: {tool_type}"

    except Exception as e:
        return f"Error executing external tool '{name}': {e}"


# ── Schema from function signature ──────────────────────────

_TYPE_MAP = {str: "string", int: "integer", bool: "boolean", float: "number"}


def _unwrap_optional(hint):
    """If hint is Optional[X] (Union[X, None]), return X."""
    args = getattr(hint, "__args__", None)
    if args and type(None) in args:
        real = [a for a in args if a is not type(None)]
        if len(real) == 1:
            return real[0]
    return hint


def _schema_for_type(hint) -> dict:
    origin = get_origin(hint)
    if origin is list:
        args = get_args(hint)
        item_hint = _unwrap_optional(args[0]) if args else str
        return {
            "type": "array",
            "items": _schema_for_type(item_hint),
        }
    if origin is dict or hint is dict:
        return {"type": "object", "additionalProperties": True}
    if origin is Literal:
        return {"type": "string", "enum": list(get_args(hint))}
    return {"type": _TYPE_MAP.get(hint, "string")}  # type: ignore[arg-type]


def _parse_param_descriptions(docstring: str) -> dict[str, str]:
    """Extract :param name: description lines from docstring."""
    descs: dict[str, str] = {}
    if not docstring:
        return descs
    for line in docstring.splitlines():
        line = line.strip()
        if line.startswith(":param "):
            rest = line[7:]
            if ":" in rest:
                name, desc = rest.split(":", 1)
                descs[name.strip()] = desc.strip()
    return descs


def _fn_to_schema(name: str, fn) -> dict:
    """Introspect function → {name, description, parameters} for LLM."""
    doc = inspect.getdoc(fn) or ""
    description = doc if name == "timer" else doc.split("\n")[0]
    param_descs = _parse_param_descriptions(doc)
    hints = get_type_hints(fn)
    sig = inspect.signature(fn)
    properties: dict[str, dict] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        # Skip injected context params (keyword-only, never exposed to LLM)
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            continue
        raw_hint = hints.get(pname)
        hint = _unwrap_optional(raw_hint) if raw_hint else raw_hint
        prop: dict = _schema_for_type(hint)
        if pname in param_descs:
            prop["description"] = param_descs[pname]
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        properties[pname] = prop
        # Positional with no default → required
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _without_background_param(schema: dict) -> dict:
    """Return a delegate_task schema copy without the background option."""
    if schema.get("name") != "delegate_task":
        return schema
    schema = {
        **schema,
        "parameters": {
            **schema.get("parameters", {}),
            "properties": dict(schema.get("parameters", {}).get("properties", {})),
        },
    }
    schema["parameters"]["properties"].pop("background", None)
    required = schema["parameters"].get("required")
    if isinstance(required, list):
        schema["parameters"]["required"] = [r for r in required if r != "background"]
    return schema


async def _workspace_attached_servers(user_id: str, workspace: str) -> list[str] | None:
    if not user_id or not workspace:
        return None
    from cptr.models import Workspace

    ws = await Workspace.get_by_path(user_id, workspace)
    if not ws:
        return None
    data = ws.data or {}
    if "toolServers" not in data:
        return None
    raw = data.get("toolServers")
    if not isinstance(raw, list):
        return None
    return [str(item) for item in raw if item]


async def get_tool_list(
    builtin_tools: dict | None = None, workspace: str = "", user_id: str = ""
) -> list[dict]:
    """Return tool schemas for the LLM.

    Automatically includes browser tools when browser.enabled is true,
    and external tool server tools when configured.
    """
    tools = dict(TOOLS)
    background_subagents_enabled = False
    try:
        from cptr.models import Config

        memory_enabled = (await Config.get("memory.enabled")) not in (False, "false", "0")
        if not memory_enabled:
            tools.pop("update_memory", None)
        skills_enabled = (await Config.get("skills.enabled")) not in (False, "false", "0")
        skills_tool_enabled = (await Config.get("skills.tool_enabled")) not in (
            False,
            "false",
            "0",
        )
        if not skills_enabled:
            tools.pop("view_skill", None)
            tools.pop("manage_skill", None)
        elif not skills_tool_enabled:
            tools.pop("manage_skill", None)
        if (await Config.get("browser.enabled")) in (True, "true", "1"):
            tools.update(BROWSER_TOOLS)
        if (await Config.get("subagents.enabled")) in (True, "true", "1"):
            tools.update(SUBAGENT_TOOLS)
            background_subagents_enabled = (await Config.get("subagents.background_enabled")) in (
                True,
                "true",
                "1",
            )
        images_generation_enabled = (await Config.get("images.generation_enabled")) in (
            True,
            "true",
            "1",
        )
        images_edit_enabled = (await Config.get("images.edit_enabled")) in (True, "true", "1")
        if not (images_generation_enabled or images_edit_enabled):
            tools.pop("image_generate", None)
    except Exception:
        tools.pop("image_generate", None)
        pass

    disabled_tools = disabled_builtin_tool_names(builtin_tools)
    if not workspace:
        disabled_tools |= GLOBAL_CHAT_DISABLED_TOOLS
    if disabled_tools:
        tools = {name: tool for name, tool in tools.items() if name not in disabled_tools}

    schemas = [_fn_to_schema(name, t["fn"]) for name, t in tools.items()]
    if not background_subagents_enabled:
        schemas = [_without_background_param(s) for s in schemas]

    # Add external tool server schemas (filtered per workspace)
    try:
        cache = await _load_tool_servers()
        attached = await _workspace_attached_servers(user_id, workspace)
        for tool_info in cache["tools"].values():
            if not server_allowed_in_workspace(tool_info.get("server") or {}, attached):
                continue
            schemas.append(tool_info["spec"])
    except Exception:
        pass

    return schemas


async def execute_tool(name: str, args: dict, __context__: dict) -> str:
    """Execute a tool by name, injecting execution context."""
    info = ALL_TOOLS.get(name)
    if info:
        if not __context__.get("workspace") and name in GLOBAL_CHAT_DISABLED_TOOLS:
            return f"Error: tool requires an open workspace: {name}"
        if not is_builtin_tool_enabled(name, __context__.get("builtin_tools")):
            return f"Error: tool disabled: {name}"
        fn = info["fn"]
        args = dict(args)
        args.pop("workspace", None)
        try:
            sig = inspect.signature(fn)
            if "__context__" in sig.parameters:
                return await fn(**args, __context__=__context__)
            else:
                # Legacy tools: inject workspace directly
                return await fn(**args, workspace=__context__["workspace"])
        except Exception as e:
            return f"Error executing {name}: {e}"

    # Check external tool servers
    cache = await _load_tool_servers()
    if name in cache["tools"]:
        attached = await _workspace_attached_servers(
            str(__context__.get("user_id") or ""),
            str(__context__.get("workspace") or ""),
        )
        if not server_allowed_in_workspace(cache["tools"][name].get("server") or {}, attached):
            return f"Error: tool '{name}' is not attached to this workspace"
        return await _execute_external_tool(name, args)

    return f"Error: unknown tool: {name}"
