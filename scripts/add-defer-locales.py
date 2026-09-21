#!/usr/bin/env python3
"""Add the "run later" when-picker keys to every translated locale.

The dashboard's defer block (dashboard.defer … dashboard.deferCancel) only ever
existed in en.json: the other nine locales fall back to raw key strings, so the
clock button, the Schedule button and the picker labels would all render as
`dashboard.deferSubmit` in any language but English.

Note that en.json is the only locale with a `dashboard.` namespace at all: the
translated files carry no dashboard key, so every board label falls back to
English via i18next's `fallbackLng`. This script translates the defer group (and
nothing else yet), writing it after `connections.providerType` — the last key
that sorts before `dashboard.` in en.json.

Each file keeps its own formatting (tab indent, `"key": "value",`) rather than
being re-serialised, and the now unused `dashboard.deferAtPlaceholder` (an older
free-text "When?" field) is dropped from en.json by hand, not carried here.

Usage:  .venv/bin/python scripts/add-defer-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

# Insertion order taken from en.json.
KEYS = [
    "dashboard.defer",
    "dashboard.deferWhen",
    "dashboard.deferDate",
    "dashboard.deferTime",
    "dashboard.deferIn15m",
    "dashboard.deferIn1h",
    "dashboard.deferIn3h",
    "dashboard.deferTomorrow",
    "dashboard.deferNextWeek",
    "dashboard.deferPast",
    "dashboard.deferNeedTime",
    "dashboard.deferNotePlaceholder",
    "dashboard.deferSubmit",
    "dashboard.deferCancel",
]

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        "dashboard.defer": "Später ausführen…",
        "dashboard.deferWhen": "Ausführen um",
        "dashboard.deferDate": "Datum",
        "dashboard.deferTime": "Uhrzeit",
        "dashboard.deferIn15m": "15 Min.",
        "dashboard.deferIn1h": "1 Stunde",
        "dashboard.deferIn3h": "3 Stunden",
        "dashboard.deferTomorrow": "Morgen um 9",
        "dashboard.deferNextWeek": "In einer Woche",
        "dashboard.deferPast": "Dieser Zeitpunkt liegt bereits in der Vergangenheit.",
        "dashboard.deferNeedTime": "Bitte zuerst Datum und Uhrzeit wählen.",
        "dashboard.deferNotePlaceholder": "Zusätzliche Anweisungen (optional)",
        "dashboard.deferSubmit": "Planen",
        "dashboard.deferCancel": "Abbrechen",
    },
    "fr": {
        "dashboard.defer": "Exécuter plus tard…",
        "dashboard.deferWhen": "Exécuter à",
        "dashboard.deferDate": "Date",
        "dashboard.deferTime": "Heure",
        "dashboard.deferIn15m": "15 min",
        "dashboard.deferIn1h": "1 heure",
        "dashboard.deferIn3h": "3 heures",
        "dashboard.deferTomorrow": "Demain 9 h",
        "dashboard.deferNextWeek": "Dans une semaine",
        "dashboard.deferPast": "Cette heure est déjà passée.",
        "dashboard.deferNeedTime": "Choisissez d'abord une date et une heure.",
        "dashboard.deferNotePlaceholder": "Instructions supplémentaires (facultatif)",
        "dashboard.deferSubmit": "Planifier",
        "dashboard.deferCancel": "Annuler",
    },
    "es": {
        "dashboard.defer": "Ejecutar más tarde…",
        "dashboard.deferWhen": "Ejecutar a las",
        "dashboard.deferDate": "Fecha",
        "dashboard.deferTime": "Hora",
        "dashboard.deferIn15m": "15 min",
        "dashboard.deferIn1h": "1 hora",
        "dashboard.deferIn3h": "3 horas",
        "dashboard.deferTomorrow": "Mañana a las 9",
        "dashboard.deferNextWeek": "En una semana",
        "dashboard.deferPast": "Esa hora ya pasó.",
        "dashboard.deferNeedTime": "Elige primero una fecha y una hora.",
        "dashboard.deferNotePlaceholder": "Instrucciones adicionales (opcional)",
        "dashboard.deferSubmit": "Programar",
        "dashboard.deferCancel": "Cancelar",
    },
    "pt-BR": {
        "dashboard.defer": "Executar depois…",
        "dashboard.deferWhen": "Executar em",
        "dashboard.deferDate": "Data",
        "dashboard.deferTime": "Horário",
        "dashboard.deferIn15m": "15 min",
        "dashboard.deferIn1h": "1 hora",
        "dashboard.deferIn3h": "3 horas",
        "dashboard.deferTomorrow": "Amanhã às 9h",
        "dashboard.deferNextWeek": "Em uma semana",
        "dashboard.deferPast": "Esse horário já passou.",
        "dashboard.deferNeedTime": "Escolha primeiro uma data e um horário.",
        "dashboard.deferNotePlaceholder": "Instruções extras (opcional)",
        "dashboard.deferSubmit": "Agendar",
        "dashboard.deferCancel": "Cancelar",
    },
    "ru": {
        "dashboard.defer": "Выполнить позже…",
        "dashboard.deferWhen": "Выполнить в",
        "dashboard.deferDate": "Дата",
        "dashboard.deferTime": "Время",
        "dashboard.deferIn15m": "15 мин",
        "dashboard.deferIn1h": "1 час",
        "dashboard.deferIn3h": "3 часа",
        "dashboard.deferTomorrow": "Завтра в 9:00",
        "dashboard.deferNextWeek": "Через неделю",
        "dashboard.deferPast": "Это время уже прошло.",
        "dashboard.deferNeedTime": "Сначала выберите дату и время.",
        "dashboard.deferNotePlaceholder": "Дополнительные инструкции (необязательно)",
        "dashboard.deferSubmit": "Запланировать",
        "dashboard.deferCancel": "Отмена",
    },
    "ja": {
        "dashboard.defer": "後で実行…",
        "dashboard.deferWhen": "実行日時",
        "dashboard.deferDate": "日付",
        "dashboard.deferTime": "時刻",
        "dashboard.deferIn15m": "15分",
        "dashboard.deferIn1h": "1時間",
        "dashboard.deferIn3h": "3時間",
        "dashboard.deferTomorrow": "明日 9時",
        "dashboard.deferNextWeek": "1週間後",
        "dashboard.deferPast": "その時刻はすでに過ぎています。",
        "dashboard.deferNeedTime": "先に日付と時刻を選択してください。",
        "dashboard.deferNotePlaceholder": "追加の指示（任意）",
        "dashboard.deferSubmit": "予約",
        "dashboard.deferCancel": "キャンセル",
    },
    "ko": {
        "dashboard.defer": "나중에 실행…",
        "dashboard.deferWhen": "실행 시각",
        "dashboard.deferDate": "날짜",
        "dashboard.deferTime": "시간",
        "dashboard.deferIn15m": "15분",
        "dashboard.deferIn1h": "1시간",
        "dashboard.deferIn3h": "3시간",
        "dashboard.deferTomorrow": "내일 오전 9시",
        "dashboard.deferNextWeek": "1주일 후",
        "dashboard.deferPast": "이미 지난 시각입니다.",
        "dashboard.deferNeedTime": "먼저 날짜와 시간을 선택하세요.",
        "dashboard.deferNotePlaceholder": "추가 지시 사항 (선택)",
        "dashboard.deferSubmit": "예약",
        "dashboard.deferCancel": "취소",
    },
    "zh-CN": {
        "dashboard.defer": "稍后运行…",
        "dashboard.deferWhen": "运行时间",
        "dashboard.deferDate": "日期",
        "dashboard.deferTime": "时间",
        "dashboard.deferIn15m": "15 分钟",
        "dashboard.deferIn1h": "1 小时",
        "dashboard.deferIn3h": "3 小时",
        "dashboard.deferTomorrow": "明天 9:00",
        "dashboard.deferNextWeek": "一周后",
        "dashboard.deferPast": "该时间已过去。",
        "dashboard.deferNeedTime": "请先选择日期和时间。",
        "dashboard.deferNotePlaceholder": "补充说明（可选）",
        "dashboard.deferSubmit": "安排",
        "dashboard.deferCancel": "取消",
    },
    "zh-TW": {
        "dashboard.defer": "稍後執行…",
        "dashboard.deferWhen": "執行時間",
        "dashboard.deferDate": "日期",
        "dashboard.deferTime": "時間",
        "dashboard.deferIn15m": "15 分鐘",
        "dashboard.deferIn1h": "1 小時",
        "dashboard.deferIn3h": "3 小時",
        "dashboard.deferTomorrow": "明天 9:00",
        "dashboard.deferNextWeek": "一週後",
        "dashboard.deferPast": "該時間已過去。",
        "dashboard.deferNeedTime": "請先選擇日期與時間。",
        "dashboard.deferNotePlaceholder": "補充說明（選填）",
        "dashboard.deferSubmit": "排程",
        "dashboard.deferCancel": "取消",
    },
}

ANCHOR = '\t"connections.providerType": '


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
        return f"{path.name}: ERROR no connections.providerType anchor"
    start = idx + 1
    end = text.index("\n", start) + 1
    line = text[start:end]
    if check:
        return f"{path.name}: MISSING {len(missing)} keys"
    if line.rstrip("\n").endswith(","):
        # a mid-file anchor: the block slots in after it
        patched = text[:end] + block(locale) + "\n" + text[end:]
    else:
        # The anchor is the file's last entry, so it cannot carry a comma. The
        # block goes in front of it instead — the anchor stays last, and the
        # block's own trailing comma is what the anchor line now follows.
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
