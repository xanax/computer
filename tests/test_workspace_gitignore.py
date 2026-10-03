"""`.cptr` auto-gitignore must not clobber a curated allowlist.

Regression: the check only accepted a line that was exactly `.cptr` or `.cptr/`,
so a project using `.cptr/*` plus `!.cptr/skills/` had a bare `.cptr` appended to
the end of the file. Git gives the *last* matching pattern precedence, so that
appended line re-ignored the whole directory and silently voided every negation —
the allowlist looked right in the file and did nothing.
"""

from __future__ import annotations

import pytest

from cptr.utils import workspace as workspace_mod
from cptr.utils.workspace import _already_ignores_cptr, ensure_cptr_gitignored


@pytest.fixture(autouse=True)
def _feature_enabled(monkeypatch):
    """Pin the feature on so these tests don't depend on the host's config."""
    monkeypatch.setattr(workspace_mod, "auto_gitignore_cptr_enabled", lambda: True)


@pytest.mark.parametrize(
    "line", [".cptr", ".cptr/", "/.cptr", "/.cptr/", ".cptr/*", "/.cptr/*"]
)
def test_recognises_equivalent_ignore_forms(line):
    assert _already_ignores_cptr(f"node_modules/\n{line}\n")


def test_unrelated_content_is_not_a_match():
    assert not _already_ignores_cptr("node_modules/\n*.log\n")


@pytest.mark.parametrize(
    "original",
    [
        ".cptr\n!.cptr/\n.cptr/*\n!.cptr/skills/\n",
        ".cptr/*\n!.cptr/skills/\n!.cptr/artifacts/\n",
    ],
)
def test_curated_allowlist_is_left_alone(git_repo, original):
    gitignore = git_repo.path / ".gitignore"
    gitignore.write_text(original, encoding="utf-8")

    ensure_cptr_gitignored(git_repo.path)

    # Byte-identical: no appended entry, no reordered lines.
    assert gitignore.read_text(encoding="utf-8") == original


def test_entry_still_added_when_absent(git_repo):
    gitignore = git_repo.path / ".gitignore"
    gitignore.write_text("node_modules/\n", encoding="utf-8")

    ensure_cptr_gitignored(git_repo.path)

    assert gitignore.read_text(encoding="utf-8") == "node_modules/\n.cptr\n"


def test_created_when_missing(git_repo):
    ensure_cptr_gitignored(git_repo.path)

    assert (git_repo.path / ".gitignore").read_text(encoding="utf-8") == ".cptr\n"


def test_missing_trailing_newline_is_handled(git_repo):
    gitignore = git_repo.path / ".gitignore"
    gitignore.write_text("node_modules/", encoding="utf-8")

    ensure_cptr_gitignored(git_repo.path)

    assert gitignore.read_text(encoding="utf-8") == "node_modules/\n.cptr\n"


def test_non_repo_is_skipped(tmp_path):
    ensure_cptr_gitignored(tmp_path)

    assert not (tmp_path / ".gitignore").exists()
