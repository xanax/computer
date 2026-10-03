#!/usr/bin/env python3
"""Translate the `dashboard.notes*` keys (the workspace notes box) into every locale.

Workspace notes are a fork addition: the sticky list that both the human (on the
workspace dashboard) and the agent (with its `add_workspace_note` tool) write to,
and that every chat in the workspace is told about. Their strings exist only in
English until this runs, so every other locale would show English mid-page. The
block is appended at the end of each file, so a patched locale differs only by
the inserted lines.

Usage:  .venv/bin/python scripts/add-workspace-notes-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

#: The block, in the order it reads on the page.
KEYS = [
    "dashboard.notesTitle",
    "dashboard.notesHint",
    "dashboard.notesPlaceholder",
    "dashboard.addNote",
    "dashboard.notesEmpty",
    "dashboard.notesByAgent",
    "dashboard.notesByYou",
]

#: The notes box wording, one row per key, one column per locale.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "dashboard.notesTitle": {
        "en": "Notes",
        "de": "Notizen",
        "es": "Notas",
        "fr": "Notes",
        "pt-BR": "Notas",
        "ru": "Заметки",
        "ja": "ノート",
        "ko": "노트",
        "zh-CN": "笔记",
        "zh-TW": "筆記",
    },
    "dashboard.notesHint": {
        "en": "Sent to every chat in this workspace — the agent can add notes of its own.",
        "de": "Wird an jeden Chat in diesem Arbeitsbereich gesendet – der Agent kann eigene Notizen hinzufügen.",
        "es": "Se envía a cada chat de este espacio; el agente también puede añadir sus propias notas.",
        "fr": "Envoyées à chaque conversation de cet espace — l’agent peut y ajouter ses propres notes.",
        "pt-BR": "Enviadas a cada chat deste espaço — o agente também pode adicionar as suas notas.",
        "ru": "Отправляются в каждый чат этого пространства — агент может добавлять свои заметки.",
        "ja": "このワークスペースのすべてのチャットに送信されます。エージェントも自分のノートを追加できます。",
        "ko": "이 워크스페이스의 모든 채팅에 전송되며, 에이전트도 직접 노트를 추가할 수 있습니다.",
        "zh-CN": "会发送到该工作区的每次聊天——智能体也可以添加自己的笔记。",
        "zh-TW": "會傳送到該工作區的每次對話——代理也可以加入自己的筆記。",
    },
    "dashboard.notesPlaceholder": {
        "en": "A note for the next chat here",
        "de": "Eine Notiz für den nächsten Chat hier",
        "es": "Una nota para el próximo chat",
        "fr": "Une note pour la prochaine conversation",
        "pt-BR": "Uma nota para o próximo chat aqui",
        "ru": "Заметка для следующего чата здесь",
        "ja": "次のチャットへのメモ",
        "ko": "다음 채팅을 위한 노트",
        "zh-CN": "留给下次聊天的笔记",
        "zh-TW": "留給下次對話的筆記",
    },
    "dashboard.addNote": {
        "en": "Add note",
        "de": "Notiz hinzufügen",
        "es": "Añadir nota",
        "fr": "Ajouter une note",
        "pt-BR": "Adicionar nota",
        "ru": "Добавить заметку",
        "ja": "ノートを追加",
        "ko": "노트 추가",
        "zh-CN": "添加笔记",
        "zh-TW": "新增筆記",
    },
    "dashboard.notesEmpty": {
        "en": "No notes yet.",
        "de": "Noch keine Notizen.",
        "es": "Aún no hay notas.",
        "fr": "Pas encore de notes.",
        "pt-BR": "Ainda não há notas.",
        "ru": "Заметок пока нет.",
        "ja": "ノートはまだありません。",
        "ko": "아직 노트가 없습니다.",
        "zh-CN": "还没有笔记。",
        "zh-TW": "還沒有筆記。",
    },
    "dashboard.notesByAgent": {
        "en": "from a chat",
        "de": "aus einem Chat",
        "es": "desde un chat",
        "fr": "depuis une conversation",
        "pt-BR": "de um chat",
        "ru": "из чата",
        "ja": "チャットから",
        "ko": "채팅에서",
        "zh-CN": "来自聊天",
        "zh-TW": "來自對話",
    },
    "dashboard.notesByYou": {
        "en": "you",
        "de": "von dir",
        "es": "tú",
        "fr": "vous",
        "pt-BR": "você",
        "ru": "вы",
        "ja": "あなた",
        "ko": "나",
        "zh-CN": "你",
        "zh-TW": "你",
    },
}


def values(locale: str) -> dict[str, str]:
    """The full notes block for one locale."""
    out: dict[str, str] = {}
    for key in KEYS:
        try:
            out[key] = TRANSLATIONS[key][locale]
        except KeyError:  # pragma: no cover - a gap in the table above
            raise SystemExit(f"{locale}: no value for {key}") from None
    return out


def block(pairs: dict[str, str]) -> str:
    return "".join(
        f"\t{json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},\n"
        for k, v in pairs.items()
    )


def patch(locale: str, path: Path, check: bool) -> str:
    text = path.read_text(encoding="utf-8")
    existing = json.loads(text)
    missing = [k for k in KEYS if k not in existing]
    if not missing:
        return f"{path.name}: already complete"
    if check:
        return f"{path.name}: MISSING {len(missing)} keys"

    pairs = values(locale)
    end = text.rstrip()
    assert end.endswith("}"), path.name
    head = end[:-1]
    comma = "" if head.rstrip().endswith(",") else ","
    body = block(pairs).rstrip()
    assert body.endswith(","), path.name
    patched = f"{head}{comma}\n{body[:-1]}\n}}\n"

    data = json.loads(patched)  # must stay valid JSON
    for key in KEYS:
        assert data[key] == pairs[key], f"{path.name}: {key}"
    assert len(data) == len(existing) + len(missing), path.name
    path.write_text(patched, encoding="utf-8")
    return f"{path.name}: added {len(missing)} keys"


def main() -> int:
    check = "--check" in sys.argv
    locales = list(TRANSLATIONS["dashboard.notesTitle"])
    out = []
    for locale in locales:
        path = LOCALES / f"{locale}.json"
        if not path.exists():
            out.append(f"{locale}: ERROR {path} not found")
            continue
        out.append(patch(locale, path, check))
    print("\n".join(out))
    if check:
        return 1 if any("MISSING" in line or "ERROR" in line for line in out) else 0
    return 1 if any("ERROR" in line for line in out) else 0


if __name__ == "__main__":
    sys.exit(main())
