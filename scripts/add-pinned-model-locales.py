"""Add the modelSelector.pin / modelSelector.unpin keys to every locale.

Mirrors scripts/add-*-locales.py in this repo: keys are flat, sorted nowhere in
particular, and appended to the end of the object. Values are kept in the same
place across locales so a missing translation is obvious in a diff.
"""

from __future__ import annotations

import json
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "cptr/frontend/src/lib/i18n/locales"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "modelSelector.pin": "Pin to top",
        "modelSelector.unpin": "Unpin",
    },
    "de": {
        "modelSelector.pin": "Oben anheften",
        "modelSelector.unpin": "Loslösen",
    },
    "es": {
        "modelSelector.pin": "Fijar arriba",
        "modelSelector.unpin": "Quitar de fijados",
    },
    "fr": {
        "modelSelector.pin": "Épingler en haut",
        "modelSelector.unpin": "Détacher",
    },
    "ja": {
        "modelSelector.pin": "上部に固定",
        "modelSelector.unpin": "固定を解除",
    },
    "ko": {
        "modelSelector.pin": "상단에 고정",
        "modelSelector.unpin": "고정 해제",
    },
    "pt-BR": {
        "modelSelector.pin": "Fixar no topo",
        "modelSelector.unpin": "Desafixar",
    },
    "ru": {
        "modelSelector.pin": "Закрепить сверху",
        "modelSelector.unpin": "Открепить",
    },
    "zh-CN": {
        "modelSelector.pin": "固定到顶部",
        "modelSelector.unpin": "取消固定",
    },
    "zh-TW": {
        "modelSelector.pin": "固定到頂部",
        "modelSelector.unpin": "取消固定",
    },
}

EN = TRANSLATIONS["en"]


def main() -> None:
    for locale, entries in TRANSLATIONS.items():
        path = LOCALES / f"{locale}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        added = [key for key in EN if key not in data]
        data.update(entries)
        path.write_text(json.dumps(data, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8")
        print(f"{locale}: +{len(added)} keys (total {len(data)})")


if __name__ == "__main__":
    main()
