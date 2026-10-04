#!/usr/bin/env python3
"""Translate the collapsed-workspace-tail keys into every locale.

The activity-aware sidebar hides its low-priority tail behind one row. Two
strings are needed and they are a fork addition, so every locale would show
English mid-list until this runs: the label, and its inverse when the tail is
already open.

"Show more" on its own would also be ambiguous next to the per-workspace chat
"Show more" that already exists, so the collapsed label carries the count of
what is behind it.

Usage:  .venv/bin/python scripts/add-workspace-tail-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

#: The block, in the order it reads in the sidebar.
KEYS = [
    "sidebar.showMoreCount",
    "sidebar.showFewer",
]

#: The wording, one row per key, one column per locale.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "sidebar.showMoreCount": {
        "en": "Show more ({{count}})",
        "de": "Mehr anzeigen ({{count}})",
        "es": "Mostrar más ({{count}})",
        "fr": "Afficher plus ({{count}})",
        "pt-BR": "Mostrar mais ({{count}})",
        "ru": "Показать ещё ({{count}})",
        "ja": "もっと見る（{{count}}）",
        "ko": "더 보기({{count}})",
        "zh-CN": "显示更多（{{count}}）",
        "zh-TW": "顯示更多（{{count}}）",
    },
    "sidebar.showFewer": {
        "en": "Show fewer",
        "de": "Weniger anzeigen",
        "es": "Mostrar menos",
        "fr": "Afficher moins",
        "pt-BR": "Mostrar menos",
        "ru": "Показать меньше",
        "ja": "表示を減らす",
        "ko": "덜 보기",
        "zh-CN": "收起",
        "zh-TW": "收合",
    },
}


def values(locale: str) -> dict[str, str]:
    """The full tail-toggle block for one locale."""
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
    locales = list(TRANSLATIONS["sidebar.showMoreCount"])
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
