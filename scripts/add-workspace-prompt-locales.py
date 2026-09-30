#!/usr/bin/env python3
"""Translate the `dashboard.prompt*` keys (the workspace prompt box) into every locale.

The workspace prompt is a fork addition: the box that shows — and edits — the
short description of what a workspace is for. Its four strings exist only in
English until this runs, so every other locale would show English mid-page.
The block is appended at the end of each file, so a patched locale differs only
by the inserted lines.

Usage:  .venv/bin/python scripts/add-workspace-prompt-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

#: The block, in the order it reads on the page.
KEYS = [
    "dashboard.promptTitle",
    "dashboard.promptHint",
    "dashboard.promptPlaceholder",
    "dashboard.promptEmpty",
]

#: The prompt box wording, one row per key, one column per locale.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "dashboard.promptTitle": {
        "en": "Workspace prompt",
        "de": "Prompt des Arbeitsbereichs",
        "es": "Prompt del espacio",
        "fr": "Invite de l’espace",
        "pt-BR": "Prompt do espaço",
        "ru": "Промпт рабочего пространства",
        "ja": "ワークスペースのプロンプト",
        "ko": "워크스페이스 프롬프트",
        "zh-CN": "工作区提示词",
        "zh-TW": "工作區提示詞",
    },
    "dashboard.promptHint": {
        "en": "Shown here, and sent at the start of every chat in this workspace.",
        "de": "Erscheint hier und wird am Anfang jedes Chats in diesem Arbeitsbereich gesendet.",
        "es": "Se muestra aquí y se envía al inicio de cada chat de este espacio.",
        "fr": "Affiché ici et envoyé au début de chaque conversation de cet espace.",
        "pt-BR": "Mostrado aqui e enviado no início de cada chat neste espaço.",
        "ru": "Показывается здесь и отправляется в начале каждого чата в этом пространстве.",
        "ja": "ここに表示され、このワークスペースのすべてのチャットの冒頭で送信されます。",
        "ko": "여기에 표시되며 이 워크스페이스의 모든 채팅 시작 부분에 전송됩니다.",
        "zh-CN": "显示在这里，并在该工作区的每次聊天开头发送。",
        "zh-TW": "顯示在這裡，並在該工作區的每次對話開頭傳送。",
    },
    "dashboard.promptPlaceholder": {
        "en": "What is this workspace for?",
        "de": "Wofür ist dieser Arbeitsbereich?",
        "es": "¿Para qué sirve este espacio?",
        "fr": "À quoi sert cet espace ?",
        "pt-BR": "Para que serve este espaço?",
        "ru": "Для чего это пространство?",
        "ja": "このワークスペースは何のため？",
        "ko": "이 워크스페이스는 무엇을 위한 곳인가요?",
        "zh-CN": "这个工作区是做什么的？",
        "zh-TW": "這個工作區是做什麼的？",
    },
    "dashboard.promptEmpty": {
        "en": "No prompt yet.",
        "de": "Noch kein Prompt.",
        "es": "Aún no hay prompt.",
        "fr": "Pas encore d’invite.",
        "pt-BR": "Ainda sem prompt.",
        "ru": "Промпта пока нет.",
        "ja": "プロンプトはまだありません。",
        "ko": "아직 프롬프트가 없습니다.",
        "zh-CN": "还没有提示词。",
        "zh-TW": "還沒有提示詞。",
    },
}


def values(locale: str) -> dict[str, str]:
    """The full prompt block for one locale."""
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
    locales = list(TRANSLATIONS["dashboard.promptTitle"])
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
