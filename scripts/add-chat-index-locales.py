#!/usr/bin/env python3
"""Add the `chat.index*` keys and the `/index` slash-command labels.

The index entry in `chats.summary` is rebuilt by `POST /api/chats/{id}/index`,
surfaced as `/index` in the composer and as a toast on `ChatPanel`.

Keys are inserted **as text** right after `chat.commandModelDesc` and not by
re-dumping the JSON, so no other line's formatting moves. Idempotent: locales
that already have the keys are left alone.

    .venv/bin/python scripts/add-chat-index-locales.py            # write
    .venv/bin/python scripts/add-chat-index-locales.py --check    # verify only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

# First anchor found wins. `en` has the `commandModel*` keys; the other locale
# files stop at `commandStatusDesc`, so that is the anchor that works everywhere.
ANCHORS = ('\t"chat.commandStatusDesc":', '\t"chat.commandStatus":')

# key -> per-locale text, in insertion order.
STRINGS: dict[str, dict[str, str]] = {
    "chat.commandIndex": {
        "en": "Index",
        "de": "Index",
        "es": "Índice",
        "fr": "Index",
        "ja": "インデックス",
        "ko": "인덱스",
        "pt-BR": "Índice",
        "ru": "Индекс",
        "zh-CN": "索引",
        "zh-TW": "索引",
    },
    "chat.commandIndexDesc": {
        "en": "Rebuild this chat's search index",
        "de": "Suchindex dieses Chats neu aufbauen",
        "es": "Reconstruir el índice de búsqueda de este chat",
        "fr": "Reconstruire l'index de recherche de ce chat",
        "ja": "このチャットの検索インデックスを再構築",
        "ko": "이 채팅의 검색 인덱스 다시 만들기",
        "pt-BR": "Reconstruir o índice de busca desta conversa",
        "ru": "Перестроить поисковый индекс этого чата",
        "zh-CN": "重建此对话的搜索索引",
        "zh-TW": "重建此對話的搜尋索引",
    },
    "chat.indexing": {
        "en": "Indexing chat...",
        "de": "Chat wird indexiert...",
        "es": "Indexando el chat...",
        "fr": "Indexation du chat...",
        "ja": "チャットをインデックス中...",
        "ko": "채팅 인덱싱 중...",
        "pt-BR": "Indexando a conversa...",
        "ru": "Индексирование чата...",
        "zh-CN": "正在为对话建立索引...",
        "zh-TW": "正在為對話建立索引...",
    },
    "chat.indexDone": {
        "en": "Search index rebuilt",
        "de": "Suchindex neu aufgebaut",
        "es": "Índice de búsqueda reconstruido",
        "fr": "Index de recherche reconstruit",
        "ja": "検索インデックスを再構築しました",
        "ko": "검색 인덱스를 다시 만들었습니다",
        "pt-BR": "Índice de busca reconstruído",
        "ru": "Поисковый индекс перестроен",
        "zh-CN": "搜索索引已重建",
        "zh-TW": "搜尋索引已重建",
    },
    "chat.indexSkipped": {
        "en": "Nothing to index yet",
        "de": "Noch nichts zu indexieren",
        "es": "Todavía no hay nada que indexar",
        "fr": "Rien à indexer pour l'instant",
        "ja": "インデックスする内容がまだありません",
        "ko": "아직 인덱싱할 내용이 없습니다",
        "pt-BR": "Nada para indexar ainda",
        "ru": "Пока нечего индексировать",
        "zh-CN": "暂无可索引的内容",
        "zh-TW": "暫無可索引的內容",
    },
    "chat.indexFailed": {
        "en": "Failed to rebuild search index",
        "de": "Suchindex konnte nicht neu aufgebaut werden",
        "es": "No se pudo reconstruir el índice de búsqueda",
        "fr": "Échec de la reconstruction de l'index de recherche",
        "ja": "検索インデックスを再構築できませんでした",
        "ko": "검색 인덱스를 다시 만들지 못했습니다",
        "pt-BR": "Falha ao reconstruir o índice de busca",
        "ru": "Не удалось перестроить поисковый индекс",
        "zh-CN": "无法重建搜索索引",
        "zh-TW": "無法重建搜尋索引",
    },
    "chat.indexNoChat": {
        "en": "Start a chat before indexing",
        "de": "Beginne einen Chat, bevor du indexierst",
        "es": "Inicia un chat antes de indexar",
        "fr": "Démarrez un chat avant d'indexer",
        "ja": "インデックスする前にチャットを開始してください",
        "ko": "인덱싱하기 전에 채팅을 시작하세요",
        "pt-BR": "Inicie uma conversa antes de indexar",
        "ru": "Начните чат перед индексированием",
        "zh-CN": "请先开始对话再建立索引",
        "zh-TW": "請先開始對話再建立索引",
    },
}


def block_for(locale: str) -> str:
    lines = []
    for key, texts in STRINGS.items():
        value = texts.get(locale)
        if value is None:
            raise SystemExit(f"missing {locale} translation for {key}")
        lines.append(f'\t"{key}": {json.dumps(value, ensure_ascii=False)},')
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify, do not write")
    args = ap.parse_args()

    problems = 0
    for path in sorted(LOCALES.glob("*.json")):
        locale = path.stem
        if locale not in STRINGS["chat.commandIndex"]:
            print(f"  skip {locale} (not a known locale)")
            continue
        text = path.read_text(encoding="utf-8")

        if args.check:
            try:
                data = json.loads(text)
            except Exception as exc:
                print(f"  {locale}: INVALID JSON — {exc}")
                problems += 1
                continue
            missing = [key for key in STRINGS if key not in data]
            if missing:
                print(f"  {locale}: missing {missing}")
                problems += 1
            else:
                print(f"  {locale}: ok ({len(STRINGS)} keys present)")
            continue

        if all(f'"{key}"' in text for key in STRINGS):
            print(f"  {locale}: already present, skipped")
            continue

        lines = text.splitlines(keepends=True)
        index = None
        for anchor in ANCHORS:
            index = next((i for i, line in enumerate(lines) if line.startswith(anchor)), None)
            if index is not None:
                break
        if index is None:
            print(f"  {locale}: ANCHOR NOT FOUND — not written")
            problems += 1
            continue

        lines.insert(index + 1, block_for(locale))
        path.write_text("".join(lines), encoding="utf-8")
        json.loads(path.read_text(encoding="utf-8"))  # fail loudly if we broke it
        print(f"  {locale}: inserted {len(STRINGS)} keys")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
