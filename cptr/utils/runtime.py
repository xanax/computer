from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
import os
import signal
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Request
from cptr.utils.gitignore import is_gitignored, load_gitignore
from cptr.utils.identity import (
    ExecutionIdentity,
    IdentityUnavailable,
    env_for,
    identity_for_request,
    preexec_for,
)

MAX_FILE_SIZE = 100 * 1024 * 1024
MATCH_PAGE_SIZE = 100
MAX_CONTENT_MATCHES_PER_FILE = 3
MAX_CONTENT_SEARCH_FILE_SIZE = 1 * 1024 * 1024

TEXT_EXTENSIONS = {
    ".bash",
    ".c",
    ".cc",
    ".cfg",
    ".clj",
    ".cmake",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".cxx",
    ".diff",
    ".dockerignore",
    ".editorconfig",
    ".el",
    ".env",
    ".erl",
    ".ex",
    ".exs",
    ".fish",
    ".gitignore",
    ".go",
    ".gradle",
    ".h",
    ".hcl",
    ".hh",
    ".hpp",
    ".hs",
    ".htm",
    ".html",
    ".hxx",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".lisp",
    ".lock",
    ".log",
    ".lua",
    ".makefile",
    ".md",
    ".ml",
    ".nix",
    ".patch",
    ".php",
    ".pl",
    ".py",
    ".r",
    ".rb",
    ".rs",
    ".sbt",
    ".scala",
    ".sh",
    ".sql",
    ".svg",
    ".svelte",
    ".swift",
    ".tf",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".vim",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}

SEARCH_IGNORE_DIRS = {
    ".cptr",
    ".git",
    ".DS_Store",
    ".eggs",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".svelte-kit",
    ".tox",
    ".venv",
    "__pycache__",
    "*.egg-info",
    "build",
    "dist",
    "node_modules",
    "venv",
}


class FileError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class Runtime:
    @staticmethod
    async def stat(request: Request, path: str) -> dict[str, Any]:
        return await _file(await _request_identity(request), _stat, path)

    @staticmethod
    async def list_directory(
        request: Request, path: str, dirs_only: bool = False
    ) -> dict[str, Any]:
        return await _file(await _request_identity(request), _list_directory, path, dirs_only)

    @staticmethod
    async def list_tree(request: Request, path: str, recursive: bool = False) -> dict[str, Any]:
        return await _file(await _request_identity(request), _list_tree, path, recursive)

    @staticmethod
    async def read_file(request: Request, path: str) -> dict[str, Any]:
        return await _file(await _request_identity(request), _read_file, path)

    @staticmethod
    async def extract_text(request: Request, path: str) -> dict[str, Any]:
        return await _file(await _request_identity(request), _extract_text, path)

    @staticmethod
    async def write_file(request: Request, path: str, content: str | bytes) -> dict[str, Any]:
        return await _file(await _request_identity(request), _write_file, path, content)

    @staticmethod
    async def file_matches(
        request: Request,
        query: str,
        path: str,
        show_hidden: bool = False,
        offset: int = 0,
        limit: int = MATCH_PAGE_SIZE,
    ) -> dict[str, Any]:
        return await _file(
            await _request_identity(request),
            _file_matches,
            path,
            query,
            show_hidden,
            offset,
            limit,
        )

    @staticmethod
    async def search_files(request: Request, query: str, path: str, limit: int = 20) -> dict[str, Any]:
        return await _file(await _request_identity(request), _search_files, path, query, limit)

    @staticmethod
    async def create_item(request: Request, path: str, type: str = "file") -> dict[str, Any]:
        return await _file(await _request_identity(request), _create_item, path, type)

    @staticmethod
    async def move_item(request: Request, source: str, destination: str) -> dict[str, Any]:
        return await _file(await _request_identity(request), _move_item, source, destination)

    @staticmethod
    async def delete_item(request: Request, path: str) -> dict[str, Any]:
        return await _file(await _request_identity(request), _delete_item, path)

    @staticmethod
    async def upload_file(
        request: Request, directory: str, filename: str, content: bytes
    ) -> dict[str, Any]:
        return await _file(await _request_identity(request), _upload_file, directory, filename, content)

    @staticmethod
    async def read_bytes(request: Request, path: str) -> dict[str, Any]:
        return await _file(await _request_identity(request), _read_bytes, path)

    @staticmethod
    async def stream_file(request: Request, path: str) -> dict[str, Any]:
        identity = await _request_identity(request)
        file_stat = await _file(identity, _stat, path)
        if file_stat.get("type") != "file":
            raise FileError(f"Not a file: {path}")

        try:
            preexec = preexec_for(identity)
        except IdentityUnavailable as exc:
            raise FileError(str(exc), exc.status_code) from exc

        try:
            read_fd, write_fd = os.pipe()
            pid = os.fork()
        except OSError as exc:
            raise FileError(str(exc), 500) from exc

        if pid == 0:
            os.close(read_fd)
            try:
                if preexec:
                    preexec()
                with os.fdopen(write_fd, "wb", buffering=0) as output:
                    with open(str(file_stat["path"]), "rb") as source:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                os._exit(0)
            except Exception:
                os._exit(1)

        os.close(write_fd)

        async def body():
            loop = asyncio.get_running_loop()
            complete = False
            try:
                while True:
                    chunk = await loop.run_in_executor(None, os.read, read_fd, 1024 * 1024)
                    if not chunk:
                        complete = True
                        break
                    yield chunk
            finally:
                try:
                    os.close(read_fd)
                except OSError:
                    pass
                try:
                    if not complete:
                        os.kill(pid, signal.SIGTERM)
                    os.waitpid(pid, 0)
                except (ChildProcessError, ProcessLookupError):
                    pass

        return {
            "name": file_stat["name"],
            "media_type": file_stat["media_type"],
            "size": file_stat["size"],
            "body": body(),
        }

    @staticmethod
    async def archive_files(request: Request, paths: list[str]) -> dict[str, Any]:
        return await _file(await _request_identity(request), _archive_files, paths)


async def _request_identity(request: Request) -> ExecutionIdentity:
    try:
        return await identity_for_request(request)
    except IdentityUnavailable as exc:
        raise FileError(str(exc), exc.status_code) from exc


async def _file(
    identity: ExecutionIdentity,
    fn: Callable[..., dict[str, Any]],
    *args: Any,
) -> dict[str, Any]:
    if identity.is_pam and identity.uid is not None and identity.uid != os.geteuid():
        return await _subprocess(identity, fn, *args)
    return await _same_process(fn, *args)


async def _same_process(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(fn, *args)
    except FileError:
        raise
    except PermissionError as exc:
        raise FileError(str(exc), 403) from exc
    except OSError as exc:
        raise FileError(str(exc), 400) from exc


async def _subprocess(
    identity: ExecutionIdentity,
    fn: Callable[..., dict[str, Any]],
    *args: Any,
) -> dict[str, Any]:
    try:
        preexec = preexec_for(identity)
    except IdentityUnavailable as exc:
        raise FileError(str(exc), exc.status_code) from exc
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cptr.utils.runtime",
        "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=identity.home,
        env=env_for(identity, identity.home),
        preexec_fn=preexec,
    )
    stdout, stderr = await proc.communicate(
        json.dumps({"function": fn.__name__, "args": _pack(args)}).encode()
    )
    try:
        message = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        detail = stderr.decode(errors="replace").strip() or "file operation failed"
        raise FileError(detail, 500) from exc
    if not message.get("ok"):
        raise FileError(
            message.get("error") or "file operation failed",
            int(message.get("status") or 400),
        )
    return _unpack(message.get("result") or {})


def _pack(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, list):
        return [_pack(item) for item in value]
    if isinstance(value, dict):
        return {key: _pack(item) for key, item in value.items()}
    return value


def _unpack(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"__bytes__"}:
        return base64.b64decode(value["__bytes__"])
    if isinstance(value, list):
        return [_unpack(item) for item in value]
    if isinstance(value, dict):
        return {key: _unpack(item) for key, item in value.items()}
    return value


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _missing(path: str, kind: str = "Path") -> FileError:
    return FileError(f"{kind} not found: {path}", 404)


def _detect_language(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    return {
        ".bash": "bash",
        ".c": "c",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".css": "css",
        ".cxx": "cpp",
        ".dockerfile": "dockerfile",
        ".go": "go",
        ".h": "c",
        ".hcl": "hcl",
        ".hh": "cpp",
        ".hpp": "cpp",
        ".html": "html",
        ".hxx": "cpp",
        ".java": "java",
        ".js": "javascript",
        ".json": "json",
        ".jsx": "jsx",
        ".kt": "kotlin",
        ".lua": "lua",
        ".makefile": "makefile",
        ".md": "markdown",
        ".nix": "nix",
        ".php": "php",
        ".py": "python",
        ".r": "r",
        ".rb": "ruby",
        ".rs": "rust",
        ".sh": "bash",
        ".sql": "sql",
        ".svelte": "svelte",
        ".swift": "swift",
        ".tf": "hcl",
        ".toml": "toml",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".xml": "xml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".zsh": "bash",
    }.get(ext)


def _human_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024
    return f"{size:.1f}TB"


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    if path.name.lower() in {
        "changelog",
        "dockerfile",
        "gemfile",
        "license",
        "makefile",
        "procfile",
        "rakefile",
        "readme",
    }:
        return True
    try:
        with path.open("rb") as source:
            return b"\0" not in source.read(8192)
    except OSError:
        return False


def _list_directory(path: str, dirs_only: bool = False) -> dict[str, Any]:
    """List a directory.

    Uses os.scandir so each entry costs at most one lstat(2). The previous
    Path-based version issued three to four syscalls per entry (Path.stat,
    is_symlink, is_dir) and never reused results.

    That matters enormously on non-local filesystems: on WSL's 9p bridge to a
    Windows drive (/mnt/c) a single stat costs ~1.1ms versus ~0.02ms on ext4,
    so a 100-entry folder went from ~120ms of syscall time to ~1s. scandir
    also reuses the readdir dirent type where the filesystem provides it,
    avoiding the stat entirely.

    dirs_only skips non-directories without stat'ing them, for directory
    pickers that only ever show folders.
    """
    target = _path(path)
    try:
        with os.scandir(target) as scan:
            entries = []
            for entry in scan:
                try:
                    if dirs_only:
                        if entry.is_dir(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            entries.append(
                                {
                                    "name": entry.name,
                                    "type": "directory",
                                    "size": None,
                                    "modified": datetime.fromtimestamp(
                                        st.st_mtime, tz=timezone.utc
                                    ).isoformat(),
                                }
                            )
                        continue

                    if entry.is_symlink():
                        # Follow the link for mtime, matching the previous
                        # behaviour. A broken link degraded to "file" before.
                        try:
                            st = entry.stat()
                        except OSError:
                            entries.append(
                                {"name": entry.name, "type": "file", "size": None, "modified": None}
                            )
                            continue
                        entries.append(
                            {
                                "name": entry.name,
                                "type": "symlink",
                                "size": None,
                                "modified": datetime.fromtimestamp(
                                    st.st_mtime, tz=timezone.utc
                                ).isoformat(),
                            }
                        )
                        continue

                    st = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=False)
                    entries.append(
                        {
                            "name": entry.name,
                            "type": "directory" if is_dir else "file",
                            "size": None if is_dir else st.st_size,
                            "modified": datetime.fromtimestamp(
                                st.st_mtime, tz=timezone.utc
                            ).isoformat(),
                        }
                    )
                except OSError:
                    entries.append(
                        {"name": entry.name, "type": "file", "size": None, "modified": None}
                    )
    except FileNotFoundError as exc:
        raise _missing(path) from exc
    except NotADirectoryError as exc:
        raise FileError(f"Not a directory: {path}") from exc
    except PermissionError as exc:
        raise FileError(f"Permission denied: {path}", 403) from exc
    except OSError as exc:
        raise FileError(str(exc), 400) from exc

    if dirs_only:
        entries.sort(key=lambda entry: entry["name"].lower())
    else:
        order = {"directory": 0, "symlink": 1, "file": 2}
        entries.sort(key=lambda entry: (order.get(entry["type"], 2), entry["name"].lower()))
    return {"path": str(target), "entries": entries}


_TREE_IGNORE = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".next",
        "build",
        "dist",
        ".cptr",
        ".svelte-kit",
    }
)

# Guard rails for a single listing. The wall-clock deadline is the important
# one: on WSL's 9p bridge to /mnt/c opening and reading a directory costs
# ~5ms against ~0.04ms on ext4, and a stat costs ~1-5ms against ~0.005ms, so
# the same tree is two orders of magnitude more expensive there. The ceilings
# bound pathological trees on fast local disks; the deadline bounds slow ones.
_TREE_DEADLINE = 1.0
_TREE_MAX_DIRS = 5_000
_TREE_MAX_FILES = 50_000
_TREE_MAX_STATS = 10_000
_TREE_MAX_LINES = 2_000


class _ScanBudget:
    """Shared ceiling on how much of a tree one listing will walk."""

    __slots__ = ("_deadline", "dirs", "files", "stats")

    def __init__(
        self,
        *,
        seconds: float = _TREE_DEADLINE,
        dirs: int = _TREE_MAX_DIRS,
        files: int = _TREE_MAX_FILES,
        stats: int = _TREE_MAX_STATS,
    ) -> None:
        self._deadline = time.monotonic() + seconds
        self.dirs = dirs
        self.files = files
        self.stats = stats

    def _exhausted(self) -> bool:
        return self.dirs <= 0 or self.files <= 0 or time.monotonic() > self._deadline

    def claim_dir(self) -> bool:
        """True if another directory may be opened."""
        self.dirs -= 1
        return not self._exhausted()

    def claim_file(self) -> bool:
        """True if another file may be counted."""
        self.files -= 1
        return not self._exhausted()

    def claim_stat(self) -> bool:
        """True if another size is worth a stat(2)."""
        self.stats -= 1
        return not self._exhausted()


def _count_files(root: str, budget: _ScanBudget) -> tuple[int, bool]:
    """Count regular files under root, skipping _TREE_IGNORE directories.

    Returns (count, truncated). Classifies every entry from the readdir dirent
    type instead of a stat(2) -- on 9p that is ~7us per entry versus ~1-5ms for
    a stat, and it is exactly what Path.rglob("*") plus Path.is_file() failed
    to do. Symlinked directories are not descended into, so link cycles cannot
    trap the walk.
    """
    count = 0
    stack = [root]
    while stack:
        if not budget.claim_dir():
            return count, True
        try:
            with os.scandir(stack.pop()) as scan:
                for entry in scan:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in _TREE_IGNORE:
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            count += 1
                            if not budget.claim_file():
                                return count, True
                    except OSError:
                        continue
        except OSError:
            continue
    return count, False


def _list_tree(path: str, recursive: bool = False) -> dict[str, Any]:
    """Render a directory tree as text.

    Both modes classify entries from the readdir dirent type via os.scandir and
    only stat(2) files whose size is displayed. That matters on non-local
    filesystems: the previous version counted files with Path.rglob("*") plus
    Path.is_file(), i.e. a stat per file just to decide whether it was a file,
    and it ran that walk for every subdirectory of the listing while descending
    into .git/node_modules. On WSL's 9p bridge to /mnt/c a single listing of a
    small folder spent ~0.6s there, and listing a Windows profile directory
    took minutes: 35 subdirectories alone cost ~390ms.

    The output is bounded (~1s of walking, "?" for anything not reached, "+" for
    counts that hit a ceiling) so an expensive tree degrades instead of hanging.
    """
    target = _path(path)
    if not target.is_dir():
        raise FileError(f"not a directory: {path}")

    budget = _ScanBudget()
    lines: list[str] = []
    notes: list[str] = []
    approximate = False

    if recursive:
        stack = [("", str(target))]
        while stack:
            if len(lines) >= _TREE_MAX_LINES or not budget.claim_dir():
                notes.append("(truncated: listing limit reached, narrow the path)")
                break
            rel, current = stack.pop()
            try:
                with os.scandir(current) as scan:
                    dirs: list[str] = []
                    files: list[tuple[str, int | None]] = []
                    for entry in scan:
                        if entry.name in _TREE_IGNORE:
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                dirs.append(entry.name)
                            elif budget.claim_stat():
                                size = entry.stat(follow_symlinks=False).st_size
                                files.append((entry.name, size))
                            else:
                                files.append((entry.name, None))
                        except OSError:
                            files.append((entry.name, None))
            except OSError:
                continue

            for name, size in sorted(files, key=lambda row: row[0].lower()):
                child = f"{rel}/{name}" if rel else name
                if size is None:
                    approximate = True
                lines.append(f"{child}  ({'?' if size is None else _human_size(size)})")
            # Reversed because the stack is a DFS: pop() then walks alphabetically.
            for name in sorted(dirs, key=str.lower, reverse=True):
                child = f"{rel}/{name}" if rel else name
                stack.append((child, os.path.join(current, name)))
    else:
        dirs = []
        files = []
        try:
            with os.scandir(target) as scan:
                for entry in scan:
                    if entry.name in _TREE_IGNORE:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs.append(entry.name)
                        elif budget.claim_stat():
                            size = entry.stat(follow_symlinks=False).st_size
                            files.append((entry.name, size))
                        else:
                            files.append((entry.name, None))
                    except OSError:
                        files.append((entry.name, None))
        except FileNotFoundError as exc:
            raise _missing(path) from exc
        except NotADirectoryError as exc:
            raise FileError(f"Not a directory: {path}") from exc
        except PermissionError as exc:
            raise FileError(f"Permission denied: {path}", 403) from exc
        except OSError as exc:
            raise FileError(str(exc), 400) from exc

        for name in sorted(dirs, key=str.lower):
            if not budget.claim_dir():
                lines.append(f"{name}/  (?)")
                approximate = True
                continue
            count, capped = _count_files(os.path.join(str(target), name), budget)
            approximate = approximate or capped
            suffix = "+ files" if capped else " files"
            lines.append(f"{name}/  ({count}{suffix})")
        for name, size in sorted(files, key=lambda row: row[0].lower()):
            if size is None:
                approximate = True
            lines.append(f"{name}  ({'?' if size is None else _human_size(size)})")

    if approximate:
        notes.append("(+ is a lower bound; ? was not scanned within the listing limit)")
    if not lines:
        lines.append("(empty directory)")
    return {"text": "\n".join(lines + notes)}


def _stat(path: str) -> dict[str, Any]:
    target = _path(path)
    if not target.exists():
        raise _missing(path)
    st = target.stat()
    kind = "symlink" if target.is_symlink() else "directory" if target.is_dir() else "file"
    media_type, _ = mimetypes.guess_type(str(target))
    return {
        "path": str(target),
        "name": target.name,
        "type": kind,
        "size": st.st_size if kind == "file" else None,
        "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "mode": st.st_mode,
        "media_type": media_type or "application/octet-stream",
    }


def _read_file(path: str) -> dict[str, Any]:
    target = _path(path)
    if not target.exists():
        raise _missing(path, "File")
    if not target.is_file():
        raise FileError(f"Not a file: {path}")

    size = target.stat().st_size
    if size > MAX_FILE_SIZE:
        raise FileError(f"File too large ({size} bytes). Max is {MAX_FILE_SIZE} bytes.", 413)

    is_text = _is_text_file(target)
    return {
        "path": str(target),
        "name": target.name,
        "size": size,
        "binary": not is_text,
        "content": target.read_text(encoding="utf-8", errors="replace") if is_text else None,
        "language": _detect_language(target.name) if is_text else None,
    }


def _extract_text(path: str) -> dict[str, Any]:
    target = _path(path)
    if not target.exists():
        raise _missing(path, "File")
    if not target.is_file():
        raise FileError(f"Not a file: {path}")
    try:
        from cptr.utils.documents import extract_by_path
    except ImportError as exc:
        raise FileError(f"reading {target.suffix} files requires: {exc}") from exc

    try:
        return {"text": extract_by_path(str(target)) or ""}
    except Exception as exc:
        raise FileError(f"failed to extract text from {target}: {exc}") from exc


def _write_file(path: str, content: str | bytes) -> dict[str, Any]:
    target = _path(path)
    if target.exists() and not target.is_file():
        raise FileError(f"Not a file: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _ensure_cptr_gitignored_for(target)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")
    return {"status": "saved", "path": str(target), "size": target.stat().st_size}


def _ensure_cptr_gitignored_for(path: Path) -> None:
    parts = path.parts
    if ".cptr" not in parts:
        return
    root = Path(*parts[: parts.index(".cptr")])
    if not (root / ".git").exists():
        return

    gitignore = root / ".gitignore"
    entry = ".cptr"
    content = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.exists() else ""
    if any(line.strip() in {entry, entry + "/"} for line in content.splitlines()):
        return
    if content and not content.endswith("\n"):
        content += "\n"
    gitignore.write_text(content + f"{entry}\n", encoding="utf-8")


def _create_item(path: str, type: str = "file") -> dict[str, Any]:
    target = _path(path)
    if target.exists():
        raise FileError(f"Already exists: {path}", 409)
    if type == "directory":
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    return {"status": "created", "path": str(target), "type": type}


def _move_item(source: str, destination: str) -> dict[str, Any]:
    src = _path(source)
    dst = _path(destination)
    if not src.exists():
        raise _missing(source, "Source")
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise FileError(f"Destination exists: {dst}", 409)
    src.rename(dst)
    return {"status": "moved", "source": str(src), "destination": str(dst)}


def _delete_item(path: str) -> dict[str, Any]:
    target = _path(path)
    if not target.exists():
        raise _missing(path)
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    return {"status": "deleted", "path": str(target)}


def _unique_child_path(directory: Path, filename: str) -> Path:
    safe_name = Path(filename).name or "file"
    target = directory / safe_name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(2, 10_000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise FileError(f"Could not find available name for: {filename}", 409)


def _upload_file(directory: str, filename: str, content: bytes) -> dict[str, Any]:
    target_dir = _path(directory)
    if not target_dir.is_dir():
        raise FileError(f"Not a directory: {directory}")
    target = _unique_child_path(target_dir, filename)
    target.write_bytes(content)
    return {"status": "uploaded", "path": str(target), "size": len(content)}


def _read_bytes(path: str) -> dict[str, Any]:
    target = _path(path)
    if not target.exists():
        raise _missing(path, "File")
    if not target.is_file():
        raise FileError(f"Not a file: {path}")
    media_type, _ = mimetypes.guess_type(str(target))
    return {
        "path": str(target),
        "name": target.name,
        "media_type": media_type or "application/octet-stream",
        "data": target.read_bytes(),
    }


def _archive_files(paths: list[str]) -> dict[str, Any]:
    if not paths:
        raise FileError("No paths provided")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for raw_path in paths:
            target = _path(raw_path)
            if target.is_file():
                archive.write(target, target.name)
            elif target.is_dir():
                for child in target.rglob("*"):
                    if child.is_file():
                        archive.write(child, str(child.relative_to(target.parent)))
    name = Path(paths[0]).name + ".zip" if len(paths) == 1 else "archive.zip"
    return {"name": name, "data": buf.getvalue()}


def _is_search_ignored(name: str) -> bool:
    return name in SEARCH_IGNORE_DIRS or name.endswith(".egg-info")


def _walk_match_entries(root: Path, show_hidden: bool, gitignore: tuple[Path, tuple] | None = None):
    if gitignore is None:
        gitignore = load_gitignore(root)
    ignore_base, ignore_patterns = gitignore

    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return

    for item in entries:
        try:
            item_is_dir = item.is_dir()
            if (
                _is_search_ignored(item.name)
                or (not show_hidden and item.name.startswith("."))
                or is_gitignored(item, ignore_base, ignore_patterns, is_dir=item_is_dir)
            ):
                continue
            if item.is_symlink():
                yield item, "file"
            elif item_is_dir:
                yield item, "directory"
                yield from _walk_match_entries(item, show_hidden, gitignore)
            elif item.is_file():
                yield item, "file"
        except OSError:
            continue


def _match_column(text: str, query_lower: str) -> int | None:
    index = text.lower().find(query_lower)
    if index < 0:
        return None
    return len(text[:index].encode("utf-16-le")) // 2 + 1


def _content_match(text: str, line: int, query_lower: str) -> dict[str, Any] | None:
    text = text.rstrip("\r\n")
    column = _match_column(text, query_lower)
    return {"line": line, "column": column, "text": text} if column is not None else None


def _content_matches_with_rg(
    root: Path, query: str, query_lower: str, show_hidden: bool, files: set[Path]
) -> dict[Path, list[dict[str, Any]]] | None:
    rg = shutil.which("rg")
    if not rg:
        return None

    args = [
        rg,
        "--json",
        "--no-messages",
        "--fixed-strings",
        "--ignore-case",
        "--line-number",
        "--column",
        "--max-count",
        str(MAX_CONTENT_MATCHES_PER_FILE + 1),
    ]
    if show_hidden:
        args.append("--hidden")
    for ignored in SEARCH_IGNORE_DIRS:
        pattern = f"!{ignored}" if ignored == ".DS_Store" else f"!{ignored}/**"
        args.extend(("--glob", pattern))
    args.extend(("--", query, str(root)))

    try:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if completed.returncode not in (0, 1):
        return None

    matches: dict[Path, list[dict[str, Any]]] = {}
    for raw in completed.stdout.splitlines():
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if message.get("type") != "match":
            continue
        data = message["data"]
        path = Path(data["path"]["text"]).resolve()
        if path not in files:
            continue
        line_matches = matches.setdefault(path, [])
        if len(line_matches) >= MAX_CONTENT_MATCHES_PER_FILE:
            continue
        match = _content_match(data["lines"]["text"], data["line_number"], query_lower)
        if match:
            line_matches.append(match)
    return matches


def _content_matches_with_python(
    files: set[Path], query_lower: str
) -> dict[Path, list[dict[str, Any]]]:
    matches: dict[Path, list[dict[str, Any]]] = {}
    for path in files:
        try:
            if path.stat().st_size > MAX_CONTENT_SEARCH_FILE_SIZE:
                continue
            with path.open("rb") as source:
                if b"\0" in source.read(8192):
                    continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for number, text in enumerate(lines, start=1):
            match = _content_match(text, number, query_lower)
            if not match:
                continue
            file_matches = matches.setdefault(path, [])
            if len(file_matches) >= MAX_CONTENT_MATCHES_PER_FILE:
                break
            file_matches.append(match)
    return matches


def _file_matches(
    path: str,
    query: str,
    show_hidden: bool = False,
    offset: int = 0,
    limit: int = MATCH_PAGE_SIZE,
) -> dict[str, Any]:
    root = _path(path)
    if not root.exists() or not root.is_dir():
        raise _missing(path)

    query = query.strip()
    query_lower = query.lower()
    entries = list(_walk_match_entries(root, show_hidden))
    files = {
        entry.resolve() for entry, kind in entries if kind == "file" and not entry.is_symlink()
    }
    content_matches = _content_matches_with_rg(root, query, query_lower, show_hidden, files)
    if content_matches is None:
        content_matches = _content_matches_with_python(files, query_lower)

    matches: list[tuple[int, int, dict[str, Any]]] = []
    for entry, kind in entries:
        relative_path = entry.relative_to(root).as_posix()
        name_lower = entry.name.lower()
        relative_lower = relative_path.lower()
        if name_lower == query_lower:
            score = 0
        elif name_lower.startswith(query_lower):
            score = 1
        elif query_lower in name_lower:
            score = 2
        elif query_lower in relative_lower:
            score = 3
        else:
            score = 4

        entry_content_matches = content_matches.get(entry.resolve(), []) if kind == "file" else []
        name_match = score < 4
        if not name_match and not entry_content_matches:
            continue
        matches.append(
            (
                score,
                len(relative_path),
                {
                    "path": str(entry),
                    "relative_path": relative_path,
                    "name": entry.name,
                    "type": kind,
                    "name_match": name_match,
                    "content_matches": entry_content_matches,
                },
            )
        )

    matches.sort(key=lambda item: (item[0], item[1], item[2]["relative_path"].lower()))
    next_offset = offset + limit if offset + limit < len(matches) else None
    return {
        "results": [item[2] for item in matches[offset : offset + limit]],
        "next_offset": next_offset,
    }


def walk_and_rank_files(root: str | Path, query: str, limit: int = 20) -> list[dict[str, Any]]:
    root = _path(str(root))
    if not root.exists() or not root.is_dir():
        raise _missing(str(root))

    query_lower = query.strip().lower().replace("\\", "/")
    matches: list[tuple[int, int, dict[str, Any]]] = []
    max_collect = limit * 10
    ignore_base, ignore_patterns = load_gitignore(root)

    def walk(directory: Path, depth: int = 0) -> None:
        if depth > 8 or len(matches) >= max_collect:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return
        for item in entries:
            item_is_dir = item.is_dir()
            if (
                item.name in SEARCH_IGNORE_DIRS
                or item.name.startswith(".")
                or is_gitignored(item, ignore_base, ignore_patterns, is_dir=item_is_dir)
            ):
                continue
            if len(matches) >= max_collect:
                return

            name_lower = item.name.lower()
            if query_lower and query_lower in name_lower:
                score = (
                    0
                    if name_lower == query_lower
                    else 1
                    if name_lower.startswith(query_lower)
                    else 2
                )
            elif not query_lower:
                score = 2
            else:
                score = None

            if score is not None:
                matches.append(
                    (
                        score,
                        len(item.name),
                        {
                            "path": str(item),
                            "name": item.name,
                            "type": "directory" if item_is_dir else "file",
                        },
                    )
                )

            if item_is_dir:
                walk(item, depth + 1)

    walk(root)
    matches.sort(key=lambda match: (match[0], match[1]))
    return [match[2] for match in matches[:limit]]


def _search_files(path: str, query: str, limit: int = 20) -> dict[str, Any]:
    return {"results": walk_and_rank_files(path, query, limit)}


CALLS = {
    fn.__name__: fn
    for fn in (
        _archive_files,
        _create_item,
        _delete_item,
        _extract_text,
        _file_matches,
        _list_directory,
        _list_tree,
        _move_item,
        _read_bytes,
        _read_file,
        _search_files,
        _stat,
        _upload_file,
        _write_file,
    )
}


def _main() -> int:
    try:
        message = json.loads(sys.stdin.read())
        name = message.get("function")
        if name not in CALLS:
            raise FileError(f"Unsupported file operation: {name}")
        result = CALLS[name](*_unpack(message.get("args") or []))
        print(json.dumps({"ok": True, "result": _pack(result)}, ensure_ascii=False))
        return 0
    except FileError as exc:
        status = exc.status_code
        error = str(exc)
    except FileNotFoundError as exc:
        status = 404
        error = str(exc)
    except FileExistsError as exc:
        status = 409
        error = str(exc)
    except PermissionError as exc:
        status = 403
        error = str(exc)
    except OSError as exc:
        status = 400
        error = str(exc)
    except Exception as exc:
        status = 400
        error = str(exc)
    print(json.dumps({"ok": False, "status": status, "error": error}))
    return 1


if __name__ == "__main__" and "--stdio" in sys.argv:
    raise SystemExit(_main())
