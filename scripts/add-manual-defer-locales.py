#!/usr/bin/env python3
"""Add the "I'll do it myself" keys to every translated locale.

`dashboard.deferCancel` now exists in all ten locales (see
add-defer-locales.py), so it is the anchor the new block slots in behind. The
new keys are:

  dashboard.deferManual      the checkbox in the defer form
  dashboard.deferManualHint  what the checkbox means ("nothing runs")
  dashboard.reminderWaiting  row detail while a manual row is still ahead
  dashboard.reminderDue      row detail once its moment has arrived

They live in the defer group rather than beside `dashboard.jobScheduled`
because only en.json carries any `dashboard.*` key at all: the other nine fall
back to English via i18next, so anything inserted here has to be insertable
against a key those files already have.

Each file keeps its own formatting (tab indent, `"key": "value",`) rather than
being re-serialised, and nothing but the new keys is allowed to change.

Usage:  .venv/bin/python scripts/add-manual-defer-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

KEYS = [
    "dashboard.deferManual",
    "dashboard.deferManualHint",
    "dashboard.reminderWaiting",
    "dashboard.reminderDue",
]

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        "dashboard.deferManual": "Mache ich selbst",
        "dashboard.deferManualHint": "Es läuft nichts – die Aufgabe wartet auf dich.",
        "dashboard.reminderWaiting": "wartet auf dich",
        "dashboard.reminderDue": "jetzt fällig",
    },
    "fr": {
        "dashboard.deferManual": "Je le ferai moi-même",
        "dashboard.deferManualHint": "Rien ne s'exécute : la ligne vous attend.",
        "dashboard.reminderWaiting": "vous attend",
        "dashboard.reminderDue": "à faire maintenant",
    },
    "es": {
        "dashboard.deferManual": "Lo hago yo",
        "dashboard.deferManualHint": "No se ejecuta nada: la fila te espera.",
        "dashboard.reminderWaiting": "te espera",
        "dashboard.reminderDue": "ahora toca",
    },
    "pt-BR": {
        "dashboard.deferManual": "Eu mesmo faço",
        "dashboard.deferManualHint": "Nada é executado: a linha espera por você.",
        "dashboard.reminderWaiting": "esperando por você",
        "dashboard.reminderDue": "é agora",
    },
    "ru": {
        "dashboard.deferManual": "Сделаю сам",
        "dashboard.deferManualHint": "Ничего не запускается — дело ждёт вас.",
        "dashboard.reminderWaiting": "ждёт вас",
        "dashboard.reminderDue": "пора делать",
    },
    "ja": {
        "dashboard.deferManual": "自分でやる",
        "dashboard.deferManualHint": "実行はされず、この行はあなたを待ちます。",
        "dashboard.reminderWaiting": "あなた待ち",
        "dashboard.reminderDue": "そろそろです",
    },
    "ko": {
        "dashboard.deferManual": "내가 직접 할게요",
        "dashboard.deferManualHint": "실행되지 않고 이 항목이 당신을 기다립니다.",
        "dashboard.reminderWaiting": "당신 차례",
        "dashboard.reminderDue": "지금 할 때",
    },
    "zh-CN": {
        "dashboard.deferManual": "我自己做",
        "dashboard.deferManualHint": "不会自动执行，这一行等你来做。",
        "dashboard.reminderWaiting": "等你来做",
        "dashboard.reminderDue": "现在该做了",
    },
    "zh-TW": {
        "dashboard.deferManual": "我自己做",
        "dashboard.deferManualHint": "不會自動執行，這一列等你來做。",
        "dashboard.reminderWaiting": "等你來做",
        "dashboard.reminderDue": "現在該做了",
    },
}

ANCHOR = '\t"dashboard.deferCancel": '


def block(locale: str) -> str:
    """The JSON text of the new keys, tab-indented, each line ending in a comma."""
    t = TRANSLATIONS[locale]
    lines = []
    for key in KEYS:
        k = json.dumps(key, ensure_ascii=False)
        v = json.dumps(t[key], ensure_ascii=False)
        lines.append(f"\t{k}: {v},")
    return "\n".join(lines)


def patch(locale: str, path: Path, check: bool) -> str:
    text = path.read_text(encoding="utf-8")
    existing = json.loads(text)
    missing = [k for k in KEYS if k not in existing]
    if not missing:
        return f"{path.name}: already complete"
    idx = text.find("\n" + ANCHOR)  # anchored to a line start, not a substring
    if idx < 0:
        return f"{path.name}: ERROR no dashboard.deferCancel anchor"
    start = idx + 1
    end = text.index("\n", start) + 1
    line = text[start:end]
    if check:
        return f"{path.name}: MISSING {len(missing)} keys"
    if line.rstrip("\n").endswith(","):
        # A mid-file anchor: the block slots in behind it.
        patched = text[:end] + block(locale) + "\n" + text[end:]
    else:
        # The anchor is the file's last entry, so it cannot carry a comma. The
        # block goes in front of it instead.
        patched = text[:start] + block(locale) + "\n" + text[start:]
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
