"""Mutation harness: prove the auto-continue DB tests actually bite.

A test that cannot fail is worse than no test -- it reads as coverage while
proving nothing. Each mutation below disables one safety guard in
``cptr/utils/auto_continue.py`` and asserts that the DB test suite FAILS.

Run: ``.venv/bin/python scripts/mutate-auto-continue.py``
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "cptr" / "utils" / "auto_continue.py"
TESTS = ["tests/test_auto_continue_db.py"]

MUTATIONS = [
    (
        "run cap removed",
        'if state["runs"] >= cap:',
        "if False:",
    ),
    (
        "run cap off-by-one (allows cap+1 runs)",
        'if state["runs"] >= cap:',
        "if state[\"runs\"] > cap:",
    ),
    (
        "stacking guard removed",
        'if message.role == "user" and (message.meta or {}).get("auto"):',
        "if False:",
    ),
    (
        "internal-chat guard removed",
        "if is_internal_chat(chat.meta):",
        "if False:",
    ),
    (
        "stop-phrase guard removed",
        "if said_stop(assistant_plain_text(content, output_items)):",
        "if False:",
    ),
    (
        "enabled flag ignored (config accepted while disabled)",
        "if not isinstance(config, dict) or not config.get(\"enabled\"):",
        "if not isinstance(config, dict):",
    ),
    (
        "custom prompt ignored (always default)",
        "auto_continue_prompt(config)",
        "DEFAULT_PROMPT",
    ),
]


def run_tests() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *TESTS, "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stdout[-400:]


def main() -> int:
    original = TARGET.read_text()
    survived: list[str] = []

    try:
        for name, old, new in MUTATIONS:
            if old not in original:
                print(f"  SKIP  {name}: pattern not found -> {old!r}")
                survived.append(name + " (pattern missing)")
                continue

            mutated = original.replace(old, new, 1)
            if mutated == original:
                print(f"  SKIP  {name}: replacement was a no-op")
                survived.append(name)
                continue

            TARGET.write_text(mutated)
            passed, tail = run_tests()

            if passed:
                print(f"  ALIVE {name}  <-- tests did NOT catch this")
                survived.append(name)
            else:
                first = next(
                    (ln for ln in tail.splitlines() if ln.startswith("FAILED") or " failed" in ln),
                    "",
                )
                print(f"  killed  {name}  ({first.strip()[:78]})")
    finally:
        TARGET.write_text(original)
        print(f"\nsource restored: {TARGET.read_text() == original}")

    print(f"\n{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)} mutants killed")
    if survived:
        print("survivors:")
        for name in survived:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())