"""Structural guard: every clean-completion exit must offer auto-continue.

``chat_task.py`` finishes a turn in three different places, one per transport
(agent target, Responses/chat-completions stream, collected tool calls). When
auto-continue landed it was wired into only one of them, which made the feature
silently dead on the other two -- no error, no log, just a chat that stopped
iterating depending on which provider you happened to use.

This test asserts the invariant directly: every ``task_completed_success = True``
clean-completion exit is followed by a call to ``_auto_continue_after_turn()``.
It reads the source rather than running a chat because the failure mode is a
*missing* call site, which no behavioural test with a stubbed task can see.
"""

from __future__ import annotations

import ast
from pathlib import Path

CHAT_TASK = Path(__file__).resolve().parent.parent / "cptr" / "utils" / "chat_task.py"
HELPER = "_auto_continue_after_turn"


def _source() -> str:
    return CHAT_TASK.read_text()


def _tree() -> ast.Module:
    return ast.parse(_source())


def _success_exit_lines(tree: ast.Module) -> list[int]:
    """Lines assigning ``task_completed_success = True`` (the clean exits)."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "task_completed_success" in targets and isinstance(node.value, ast.Constant):
            if node.value.value is True:
                lines.append(node.lineno)
    return sorted(lines)


def _helper_call_lines(tree: ast.Module) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name == HELPER:
            lines.append(node.lineno)
    return sorted(lines)


def test_the_helper_exists_once():
    """One helper, so the three call sites cannot drift apart."""
    tree = _tree()
    defs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == HELPER
    ]
    assert len(defs) == 1, f"expected exactly one {HELPER} definition, found {len(defs)}"


def test_there_is_more_than_one_clean_exit():
    """If this drops to one, the structural check below stops being meaningful
    and the transports have been unified -- revisit this test deliberately."""
    exits = _success_exit_lines(_tree())
    assert len(exits) >= 3, f"expected the three transport exits, found {exits}"


def test_every_clean_exit_offers_auto_continue():
    """The actual guard: no clean exit may finish a turn without checking for a
    follow-up. Catches the original bug and any future refactor that adds a
    fourth exit or drops an existing hook."""
    tree = _tree()
    exits = _success_exit_lines(tree)
    hooks = _helper_call_lines(tree)

    unhooked = []
    for exit_line in exits:
        # The hook is awaited a couple of lines after the flag is set.
        if not any(exit_line <= hook <= exit_line + 4 for hook in hooks):
            unhooked.append(exit_line)

    assert not unhooked, (
        "clean-completion exit(s) at line(s) "
        f"{unhooked} do not call {HELPER}(); auto-continue would be silently "
        "dead on that transport"
    )


def test_the_helper_is_not_called_from_an_unclean_exit():
    """Auto-continue must not fire on cancels, errors or max-iterations.

    A hook further than a few lines from a clean exit is suspicious, so count
    them: the number of hooks should match the number of clean exits.
    """
    tree = _tree()
    exits = _success_exit_lines(tree)
    hooks = _helper_call_lines(tree)

    matched = {hook for exit_line in exits for hook in hooks if exit_line <= hook <= exit_line + 4}
    assert len(matched) == len(hooks), (
        f"{len(hooks)} call(s) to {HELPER} but only {len(matched)} sit at a clean "
        "completion exit; auto-continue may be firing on an interactive or failed turn"
    )
