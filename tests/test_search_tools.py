"""Tests for the search_files tool and its ripgrep backend."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from cptr.utils import tools
from cptr.utils.tools import TOOLS, _fn_to_schema, _search_rg

from conftest import make_request

pytestmark = pytest.mark.skipif(
    shutil.which("rg") is None, reason="ripgrep is not installed"
)


class _Identity:
    """Stand-in for ExecutionIdentity: _search_rg only reads ``is_pam``."""

    is_pam = False


def _run(coro):
    return asyncio.run(coro)


def _write(root: Path, relpath: str, content: str) -> Path:
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    _write(tmp_path, "a.py", "def foo():\n    return 1\n\ndef foobar():\n    return 2\n")
    _write(tmp_path, "b.py", "def bar():\n    return foo\n")
    _write(tmp_path, "notes.md", "hello foo world\nsecond line\n")
    return tmp_path


def _rg(tree: Path, query: str, **kwargs) -> str:
    defaults = dict(
        regex=False,
        case_insensitive=False,
        include="",
        exclude="",
        type="",
        context=0,
        max_results=50,
        word=False,
        multiline=False,
        filenames_only=False,
    )
    defaults.update(kwargs)
    return _run(_search_rg(query, tree, identity=_Identity(), **defaults))


def test_search_files_schema_exposes_ripgrep_knobs():
    schema = _fn_to_schema("search_files", TOOLS["search_files"]["fn"])
    props = schema["parameters"]["properties"]
    for key in (
        "query",
        "path",
        "regex",
        "case_insensitive",
        "include",
        "exclude",
        "type",
        "context",
        "max_results",
        "word",
        "multiline",
        "filenames_only",
    ):
        assert key in props, f"missing schema key: {key}"
    assert schema["parameters"]["required"] == ["query"]


def test_search_rg_basic(tree):
    out = _rg(tree, "foo")
    lines = sorted(out.splitlines())
    assert lines == [
        "a.py:1:def foo():",
        "a.py:4:def foobar():",
        "b.py:2:    return foo",
        "notes.md:1:hello foo world",
    ]


def test_search_rg_filenames_only(tree):
    out = _rg(tree, "foo", filenames_only=True)
    assert sorted(out.splitlines()) == ["a.py", "b.py", "notes.md"]


def test_search_rg_word_boundaries(tree):
    out = _rg(tree, "foo", word=True)
    lines = sorted(out.splitlines())
    assert lines == [
        "a.py:1:def foo():",
        "b.py:2:    return foo",
        "notes.md:1:hello foo world",
    ]
    assert "foobar" not in out


def test_search_rg_exclude_glob(tree):
    out = _rg(tree, "foo", exclude="*.md")
    lines = sorted(out.splitlines())
    assert "notes.md" not in out
    assert lines == [
        "a.py:1:def foo():",
        "a.py:4:def foobar():",
        "b.py:2:    return foo",
    ]


def test_search_rg_type_filter(tree):
    out = _rg(tree, "foo", type="py")
    lines = sorted(out.splitlines())
    assert lines == [
        "a.py:1:def foo():",
        "a.py:4:def foobar():",
        "b.py:2:    return foo",
    ]


def test_search_rg_case_insensitive(tmp_path):
    _write(tmp_path, "upper.py", "FOO BAR\n")
    assert _rg(tmp_path, "foo") == "No matches found."
    assert "upper.py:1:FOO BAR" in _rg(tmp_path, "foo", case_insensitive=True)


def test_search_rg_regex(tree):
    out = _rg(tree, r"def (foo|bar)\(", regex=True)
    lines = sorted(out.splitlines())
    assert lines == ["a.py:1:def foo():", "b.py:1:def bar():"]


def test_search_rg_context_lines(tree):
    out = _rg(tree, "foo", context=1)
    # Context lines are rendered with a '-' separator (path-N-text).
    assert "a.py-2-    return 1" in out
    assert "a.py:1:def foo():" in out


def test_search_rg_max_results(tree):
    out = _rg(tree, "foo", max_results=2)
    assert len(out.splitlines()) == 2


def test_search_rg_multiline(tmp_path):
    _write(tmp_path, "m.txt", "first line\nsecond line\n")
    pattern = r"first line\nsecond"
    assert "m.txt" in _rg(tmp_path, pattern, regex=True, multiline=True)
    # Without --multiline, ripgrep rejects a literal newline in a regex.
    with pytest.raises(Exception):
        _rg(tmp_path, pattern, regex=True, multiline=False)


def test_search_rg_no_matches(tree):
    assert _rg(tree, "zzz-not-present") == "No matches found."


def test_search_files_end_to_end(tree):
    request = make_request()
    ctx = {"workspace": str(tree), "request": request, "user_id": None}
    out = _run(tools.search_files(query="foo", __context__=ctx))
    assert "a.py:1:def foo():" in out


def test_search_files_requires_request(tree):
    ctx = {"workspace": str(tree), "request": None, "user_id": None}
    out = _run(tools.search_files(query="foo", __context__=ctx))
    assert "request context unavailable" in out
