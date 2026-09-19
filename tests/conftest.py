"""Shared fixtures for cptr's test suite."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import Request


@dataclass
class Repo:
    """A small helper wrapping a throwaway git repository."""

    path: Path

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=check,
            capture_output=True,
            text=True,
        )

    def commit(self, relpath: str, content: str, message: str) -> str:
        """Write a file and commit it. Returns the full commit hash."""
        target = self.path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git("add", "--", relpath)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Repo:
    """Create an initialized, committer-configured git repository."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    repo = Repo(tmp_path)
    repo.git("config", "user.name", "Test User")
    repo.git("config", "user.email", "test@example.com")
    repo.git("config", "commit.gpgSign", "false")
    repo.git("config", "tag.gpgSign", "false")
    return repo


def make_request() -> Request:
    """Build a minimal Starlette request with an empty auth state.

    Tools that use ``Runtime.*`` need a request whose ``state.auth`` is unset;
    in non-PAM mode this resolves to the current process identity.
    """
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/__test__",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("test", 0),
            "scheme": "http",
        }
    )
    request.state.auth = None
    return request
