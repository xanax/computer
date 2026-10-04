"""Find persisted tool_calls that would serialise with an empty function.name.

Rebuilds each message's model-visible history with the real
`_output_items_to_messages`, then checks the OpenAI Chat Completions shape
that `_to_openai_messages` emits -- the request the "Stealth" provider 400s on.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cptr.utils.chat_task import _output_items_to_messages  # noqa: E402

DB = Path.home() / ".cptr" / "app.db"


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    empty_name: list[tuple] = []
    empty_id: list[tuple] = []
    checked = 0

    for mid, chat_id, output in conn.execute(
        "select id, chat_id, coalesce(output,'') from chat_messages "
        "where output not in ('','null')"
    ):
        try:
            items = json.loads(output)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        checked += 1
        for msg in _output_items_to_messages(items, mid):
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name")
                if not isinstance(name, str) or not name.strip():
                    empty_name.append(
                        (mid[:8], chat_id[:8], tc.get("id"), repr(name),
                         json.dumps(fn.get("arguments"))[:70])
                    )
                if not (tc.get("id") or "").strip():
                    empty_id.append((mid[:8], repr(name)))

    print(f"messages scanned: {checked}")
    print(f"tool_calls with empty/missing function.name: {len(empty_name)}")
    for row in empty_name[:20]:
        print("  ", row)
    print(f"tool_calls with empty id: {len(empty_id)}")
    for row in empty_id[:20]:
        print("  ", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
