#!/usr/bin/env python3
"""Add the auto-continue keys to every translated locale.

Only `en.json` carries any `plusMenu.*` key; the other nine fall back to English
via i18next, so there is no per-language anchor to slot behind. Each file
therefore gets the block appended as its last key, with a comma added to
whatever used to be last — which is why this script writes text rather than
re-serialising JSON.

The new keys:

  plusMenu.autoContinue                 the toggle, and its panel title
  plusMenu.autoContinueRuns             the run-cap field label
  plusMenu.autoContinueRunsHint         "back-to-back follow-ups"
  plusMenu.autoContinuePrompt           the static follow-up text label
  plusMenu.autoContinuePromptPlaceholder  what an empty prompt falls back to

Usage:  .venv/bin/python scripts/add-auto-continue-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

KEYS = [
    "plusMenu.autoContinue",
    "plusMenu.autoContinueRuns",
    "plusMenu.autoContinueRunsHint",
    "plusMenu.autoContinuePrompt",
    "plusMenu.autoContinuePromptPlaceholder",
]

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        "plusMenu.autoContinue": "Auto-Fortsetzung",
        "plusMenu.autoContinueRuns": "Höchstzahl an Durchläufen",
        "plusMenu.autoContinueRunsHint": "nacheinander folgende Aufforderungen",
        "plusMenu.autoContinuePrompt": "Folgeaufforderung",
        "plusMenu.autoContinuePromptPlaceholder": "Standardtext wird verwendet",
    },
    "fr": {
        "plusMenu.autoContinue": "Poursuite automatique",
        "plusMenu.autoContinueRuns": "Nombre max d’itérations",
        "plusMenu.autoContinueRunsHint": "relances successives",
        "plusMenu.autoContinuePrompt": "Invite de relance",
        "plusMenu.autoContinuePromptPlaceholder": "le texte par défaut est utilisé",
    },
    "es": {
        "plusMenu.autoContinue": "Continuación automática",
        "plusMenu.autoContinueRuns": "Máximo de iteraciones",
        "plusMenu.autoContinueRunsHint": "peticiones seguidas",
        "plusMenu.autoContinuePrompt": "Instrucción de seguimiento",
        "plusMenu.autoContinuePromptPlaceholder": "se usa el texto predeterminado",
    },
    "pt-BR": {
        "plusMenu.autoContinue": "Continuação automática",
        "plusMenu.autoContinueRuns": "Máximo de iterações",
        "plusMenu.autoContinueRunsHint": "pedidos seguidos",
        "plusMenu.autoContinuePrompt": "Instrução de continuação",
        "plusMenu.autoContinuePromptPlaceholder": "usa o texto padrão",
    },
    "ru": {
        "plusMenu.autoContinue": "Автопродолжение",
        "plusMenu.autoContinueRuns": "Максимум итераций",
        "plusMenu.autoContinueRunsHint": "повторных запросов подряд",
        "plusMenu.autoContinuePrompt": "Текст продолжения",
        "plusMenu.autoContinuePromptPlaceholder": "используется текст по умолчанию",
    },
    "ja": {
        "plusMenu.autoContinue": "自動継続",
        "plusMenu.autoContinueRuns": "最大反復回数",
        "plusMenu.autoContinueRunsHint": "連続する追加プロンプトの数",
        "plusMenu.autoContinuePrompt": "継続プロンプト",
        "plusMenu.autoContinuePromptPlaceholder": "空欄なら既定の文を使います",
    },
    "ko": {
        "plusMenu.autoContinue": "자동 계속",
        "plusMenu.autoContinueRuns": "최대 반복 횟수",
        "plusMenu.autoContinueRunsHint": "연속 후속 프롬프트 수",
        "plusMenu.autoContinuePrompt": "후속 프롬프트",
        "plusMenu.autoContinuePromptPlaceholder": "비워 두면 기본 문구 사용",
    },
    "zh-CN": {
        "plusMenu.autoContinue": "自动继续",
        "plusMenu.autoContinueRuns": "最多迭代轮数",
        "plusMenu.autoContinueRunsHint": "连续的后续提示",
        "plusMenu.autoContinuePrompt": "后续提示词",
        "plusMenu.autoContinuePromptPlaceholder": "留空则使用默认文本",
    },
    "zh-TW": {
        "plusMenu.autoContinue": "自動繼續",
        "plusMenu.autoContinueRuns": "最多迭代輪數",
        "plusMenu.autoContinueRunsHint": "連續的後續提示",
        "plusMenu.autoContinuePrompt": "後續提示詞",
        "plusMenu.autoContinuePromptPlaceholder": "留空則使用預設文字",
    },
    "en": {
        "plusMenu.autoContinue": "Auto-continue",
        "plusMenu.autoContinueRuns": "Max follow-ups",
        "plusMenu.autoContinueRunsHint": "back-to-back",
        "plusMenu.autoContinuePrompt": "Follow-up prompt",
        "plusMenu.autoContinuePromptPlaceholder": "Uses a built-in default",
    },
}


def block(locale: str) -> str:
    """The JSON text of the new keys, tab-indented.

    Commas go *between* entries only: the last new key is the file's last key,
    so a trailing comma would leave an illegal ``…,}`` before the closing brace.
    """
    t = TRANSLATIONS[locale]
    lines = []
    for key in KEYS:
        k = json.dumps(key, ensure_ascii=False)
        v = json.dumps(t[key], ensure_ascii=False)
        lines.append(f"\t{k}: {v}")
    return ",\n".join(lines)


def patch(locale: str, path: Path, check: bool) -> str:
    text = path.read_text(encoding="utf-8")
    existing = json.loads(text)
    missing = [k for k in KEYS if k not in existing]
    if not missing:
        return f"{path.name}: already complete"
    if check:
        return f"{path.name}: MISSING {len(missing)} keys"

    # Insert before the closing brace, so the previous last entry gains a comma.
    stripped = text.rstrip()
    if not stripped.endswith("}"):
        return f"{path.name}: ERROR unexpected file ending"
    head = stripped[: stripped.rfind("}")].rstrip()
    if head.endswith(","):
        head = head  # already fine
    else:
        head += ","
    patched = f"{head}\n{block(locale)}\n}}\n"

    data = json.loads(patched)  # must stay valid JSON
    assert all(data[k] == TRANSLATIONS[locale][k] for k in KEYS), path.name
    # Nothing but the new keys may have changed.
    assert {k: v for k, v in data.items() if k not in KEYS} == existing, path.name
    path.write_text(patched, encoding="utf-8")
    return f"{path.name}: added {len(missing)} keys"


def main() -> int:
    check = "--check" in sys.argv
    out = []
    for locale in TRANSLATIONS:
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
