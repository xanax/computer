"""Tests for the git helper functions and the git chat tools."""

from __future__ import annotations

import asyncio

from cptr.utils import git as gitlib
from cptr.utils import tools
from cptr.utils.tools import (
    BUILTIN_TOOL_GROUPS,
    GLOBAL_CHAT_DISABLED_TOOLS,
    TOOLS,
    _fn_to_schema,
)

GIT_TOOL_NAMES = ("git_status", "git_log", "git_show", "git_diff", "git_blame")


def _run(coro):
    return asyncio.run(coro)


def _ctx(repo) -> dict:
    return {"workspace": str(repo.path), "request": None, "user_id": None}


# ---------------------------------------------------------------------------
# Registry / schema wiring
# ---------------------------------------------------------------------------


def test_git_tools_are_registered():
    assert BUILTIN_TOOL_GROUPS["git"] == GIT_TOOL_NAMES
    for name in GIT_TOOL_NAMES:
        assert name in TOOLS, name
        assert TOOLS[name]["approval"] == "allow"
        assert name in GLOBAL_CHAT_DISABLED_TOOLS, name


def test_git_tool_schemas():
    status = _fn_to_schema("git_status", TOOLS["git_status"]["fn"])
    assert status["parameters"]["properties"] == {}

    log = _fn_to_schema("git_log", TOOLS["git_log"]["fn"])
    assert list(log["parameters"]["properties"]) == ["limit", "offset", "path", "grep"]

    show = _fn_to_schema("git_show", TOOLS["git_show"]["fn"])
    assert list(show["parameters"]["properties"]) == ["ref"]
    assert show["parameters"]["required"] == ["ref"]

    diff = _fn_to_schema("git_diff", TOOLS["git_diff"]["fn"])
    assert list(diff["parameters"]["properties"]) == [
        "staged",
        "file",
        "ref",
        "untracked",
    ]

    blame = _fn_to_schema("git_blame", TOOLS["git_blame"]["fn"])
    assert list(blame["parameters"]["properties"]) == ["file"]
    assert blame["parameters"]["required"] == ["file"]


# ---------------------------------------------------------------------------
# git.py helpers
# ---------------------------------------------------------------------------


def test_status_detects_states(git_repo):
    root = str(git_repo.path)
    git_repo.commit("a.txt", "one\n", "first")

    status = _run(gitlib.status(root))
    assert status["files"] == []
    assert status["ahead"] == 0

    # Unstaged modification.
    (git_repo.path / "a.txt").write_text("one\nmodified\n", encoding="utf-8")
    status = _run(gitlib.status(root))
    assert any(f["path"] == "a.txt" and f["staged"] is False for f in status["files"])

    # Staged.
    git_repo.git("add", "a.txt")
    status = _run(gitlib.status(root))
    assert any(f["path"] == "a.txt" and f["staged"] is True for f in status["files"])

    # Untracked.
    (git_repo.path / "new.txt").write_text("x\n", encoding="utf-8")
    status = _run(gitlib.status(root))
    assert any(f["path"] == "new.txt" and f["status"] == "untracked" for f in status["files"])


def test_log_with_path_and_grep_filters(git_repo):
    root = str(git_repo.path)
    git_repo.commit("a.txt", "one\n", "feat a one")
    git_repo.commit("a.txt", "one\ntwo\n", "feat a two")
    git_repo.commit("b.txt", "bee\n", "fix b")

    log = _run(gitlib.log(root))
    assert len(log) == 3
    assert log[0]["message"] == "fix b"  # newest first

    log_a = _run(gitlib.log(root, path="a.txt"))
    assert [e["message"] for e in log_a] == ["feat a two", "feat a one"]

    log_grep = _run(gitlib.log(root, grep="b"))
    assert [e["message"] for e in log_grep] == ["fix b"]


def test_diff_text_and_ref(git_repo):
    root = str(git_repo.path)
    git_repo.commit("a.txt", "one\n", "first")
    git_repo.commit("a.txt", "one\ntwo\n", "second")
    (git_repo.path / "a.txt").write_text("one\ntwo\nchanged\n", encoding="utf-8")

    diff = _run(gitlib.diff_text(root))
    assert "changed" in diff and "diff --git" in diff

    ref_diff = _run(gitlib.diff_ref_text(root, "HEAD"))
    assert "changed" in ref_diff

    structured = _run(gitlib.compare_diff(root, "HEAD~1", "HEAD"))
    assert any(f["path"] == "a.txt" for f in structured["files"])


def test_show_readable_and_structured(git_repo):
    root = str(git_repo.path)
    git_repo.commit("a.txt", "one\n", "add a")

    readable = _run(gitlib.show_readable(root, "HEAD"))
    assert "commit " in readable
    assert "Author: Test User <test@example.com>" in readable
    assert "add a" in readable

    structured = _run(gitlib.show(root, "HEAD"))
    assert structured["author"] == "Test User"
    assert structured["message"] == "add a"
    assert len(structured["short_hash"]) >= 7


def test_blame_lines(git_repo):
    root = str(git_repo.path)
    commit_hash = git_repo.commit("a.txt", "line one\nline two\n", "seed")

    entries = _run(gitlib.blame(root, "a.txt"))
    assert [e["text"] for e in entries] == ["line one", "line two"]
    for entry in entries:
        assert entry["author"] == "Test User"
        assert entry["hash"] == commit_hash


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------


def test_git_status_tool(git_repo):
    git_repo.commit("a.txt", "one\n", "first")
    out = _run(tools.git_status(__context__=_ctx(git_repo)))
    assert out.startswith("On branch ")
    assert "Working tree clean" in out


def test_git_log_tool(git_repo):
    git_repo.commit("a.txt", "one\n", "first commit")
    git_repo.commit("a.txt", "two\n", "second commit")
    out = _run(tools.git_log(limit=1, __context__=_ctx(git_repo)))
    assert "second commit" in out
    assert "first commit" not in out


def test_git_show_tool(git_repo):
    git_repo.commit("a.txt", "one\n", "initial")
    out = _run(tools.git_show(ref="HEAD", __context__=_ctx(git_repo)))
    assert "Author: Test User <test@example.com>" in out
    assert "initial" in out
    # Must be human-readable: no NUL separators from --format parsing.
    assert "\x00" not in out


def test_git_diff_tool(git_repo):
    git_repo.commit("a.txt", "one\n", "first")
    out = _run(tools.git_diff(ref="HEAD", __context__=_ctx(git_repo)))
    assert out == "No changes."

    (git_repo.path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    out = _run(tools.git_diff(ref="HEAD", __context__=_ctx(git_repo)))
    assert "two" in out


def test_git_blame_tool(git_repo):
    git_repo.commit("a.txt", "hello\n", "seed")
    out = _run(tools.git_blame(file="a.txt", __context__=_ctx(git_repo)))
    assert "hello" in out


def test_git_tools_require_workspace():
    ctx = {"workspace": "", "request": None, "user_id": None}
    out = _run(tools.git_status(__context__=ctx))
    assert "require an open workspace" in out


def test_git_tools_not_a_repo(tmp_path):
    ctx = {"workspace": str(tmp_path), "request": None, "user_id": None}
    out = _run(tools.git_status(__context__=ctx))
    assert out == "Not a git repository."
