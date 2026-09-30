#!/usr/bin/env python3
"""Add the `tasks.*` keys for the unified task board to every locale.

The Tasks tab (/scheduled) and the dashboard's task list share one vocabulary.
Most of it already exists per locale under `automations.*` / `dashboard.*` — the
same words the schedules panel used — so those are *copied* out of each file
rather than re-translated, and only the genuinely new wording (a task that is
neither a todo nor a schedule: "Reminder", "Deferred", "A model does it") is
translated by hand below.

The block is appended at the end of each file, so the only difference in a
patched locale is the inserted lines.

Usage:  .venv/bin/python scripts/add-tasks-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

#: Order of the inserted block (mirrors the component's reading order).
KEYS = [
    "tasks.title",
    "tasks.manage",
    "tasks.toggleSidebar",
    "tasks.filter",
    "tasks.all",
    "tasks.open",
    "tasks.review",
    "tasks.done",
    "tasks.runs",
    "tasks.newTask",
    "tasks.create",
    "tasks.createBtn",
    "tasks.titlePlaceholder",
    "tasks.instructions",
    "tasks.promptPlaceholder",
    "tasks.selectWorkspace",
    "tasks.forMe",
    "tasks.forModel",
    "tasks.pastTime",
    "tasks.untitled",
    "tasks.approve",
    "tasks.reopen",
    "tasks.runNow",
    "tasks.cancelRun",
    "tasks.delete",
    "tasks.deleteConfirm",
    "tasks.deleted",
    "tasks.paused",
    "tasks.needsReview",
    "tasks.lastRun",
    "tasks.reminder",
    "tasks.deferred",
    "tasks.noTasks",
    "tasks.noMatches",
    "tasks.noRuns",
    "tasks.viewChat",
    "tasks.cancel",
    "tasks.saving",
    "tasks.failedToLoad",
    "tasks.failedToRun",
    "tasks.failedToToggle",
    "tasks.failedToReview",
    "tasks.failedToCancel",
    "tasks.failedToDelete",
    "tasks.failedToCreate",
    # Schedule wording, shown by ScheduleDropdown and the row's schedule label.
    "tasks.schedule",
    "tasks.once",
    "tasks.hourly",
    "tasks.daily",
    "tasks.weekly",
    "tasks.monthly",
    "tasks.custom",
    "tasks.time",
    "tasks.day",
    "tasks.dayMo",
    "tasks.dayTu",
    "tasks.dayWe",
    "tasks.dayTh",
    "tasks.dayFr",
    "tasks.daySa",
    "tasks.daySu",
    "tasks.model",
]

#: task key → the key this locale already has for the same idea.
COPY = {
    "tasks.toggleSidebar": "automations.toggleSidebar",
    "tasks.filter": "automations.filter",
    "tasks.all": "automations.all",
    "tasks.runs": "automations.runs",
    "tasks.newTask": "automations.newAutomation",
    "tasks.create": "automations.create",
    "tasks.createBtn": "automationModal.createBtn",
    "tasks.titlePlaceholder": "automationModal.titlePlaceholder",
    "tasks.instructions": "automationModal.instructions",
    "tasks.promptPlaceholder": "automationModal.promptPlaceholder",
    "tasks.selectWorkspace": "automationModal.selectWorkspace",
    "tasks.forMe": "dashboard.deferManual",
    "tasks.pastTime": "dashboard.deferPast",
    "tasks.runNow": "automations.runNow",
    "tasks.deleteConfirm": "automations.deleteConfirm",
    "tasks.deleted": "automations.deleted",
    "tasks.paused": "automations.paused",
    "tasks.noTasks": "automations.noAutomations",
    "tasks.noMatches": "automations.noMatches",
    "tasks.noRuns": "automations.noRuns",
    "tasks.viewChat": "automations.viewChat",
    "tasks.cancel": "automationModal.cancel",
    "tasks.saving": "automationModal.saving",
    "tasks.failedToLoad": "automations.failedToLoad",
    "tasks.failedToRun": "automations.failedToRun",
    "tasks.failedToToggle": "automations.failedToToggle",
    "tasks.failedToDelete": "automations.failedToDelete",
    "tasks.failedToCreate": "automationModal.failedToSave",
    # The schedule editor's own words: identical idea, so reuse the translation.
    "tasks.schedule": "automations.schedule",
    "tasks.once": "automations.once",
    "tasks.hourly": "automations.hourly",
    "tasks.daily": "automations.daily",
    "tasks.weekly": "automations.weekly",
    "tasks.monthly": "automations.monthly",
    "tasks.custom": "automations.custom",
    "tasks.time": "automations.time",
    "tasks.day": "automations.day",
    "tasks.dayMo": "automations.dayMo",
    "tasks.dayTu": "automations.dayTu",
    "tasks.dayWe": "automations.dayWe",
    "tasks.dayTh": "automations.dayTh",
    "tasks.dayFr": "automations.dayFr",
    "tasks.daySa": "automations.daySa",
    "tasks.daySu": "automations.daySu",
    "tasks.model": "automations.model",
}

#: The new wording: one row per key, one column per locale.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "tasks.title": {
        "de": "Aufgaben",
        "es": "Tareas",
        "fr": "Tâches",
        "pt-BR": "Tarefas",
        "ru": "Задачи",
        "ja": "タスク",
        "ko": "작업",
        "zh-CN": "任务",
        "zh-TW": "任務",
    },
    "tasks.manage": {
        "de": "Aufgaben",
        "es": "Tareas",
        "fr": "Tâches",
        "pt-BR": "Tarefas",
        "ru": "Задачи",
        "ja": "タスク",
        "ko": "작업",
        "zh-CN": "任务",
        "zh-TW": "任務",
    },
    "tasks.open": {
        "de": "Offen",
        "es": "Abiertas",
        "fr": "Ouvertes",
        "pt-BR": "Abertas",
        "ru": "Открытые",
        "ja": "未完了",
        "ko": "열림",
        "zh-CN": "未完成",
        "zh-TW": "未完成",
    },
    "tasks.done": {
        "de": "Erledigt",
        "es": "Hechas",
        "fr": "Terminées",
        "pt-BR": "Concluídas",
        "ru": "Готово",
        "ja": "完了",
        "ko": "완료",
        "zh-CN": "已完成",
        "zh-TW": "已完成",
    },
    "tasks.forModel": {
        "de": "Ein Modell erledigt es",
        "es": "Lo hace un modelo",
        "fr": "Un modèle s'en charge",
        "pt-BR": "Um modelo faz",
        "ru": "Выполнит модель",
        "ja": "モデルが実行します",
        "ko": "모델이 실행합니다",
        "zh-CN": "由模型执行",
        "zh-TW": "由模型執行",
    },
    "tasks.approve": {
        "de": "Genehmigen",
        "es": "Aprobar",
        "fr": "Approuver",
        "pt-BR": "Aprovar",
        "ru": "Одобрить",
        "ja": "承認",
        "ko": "승인",
        "zh-CN": "批准",
        "zh-TW": "批准",
    },
    "tasks.reopen": {
        "de": "Wieder öffnen",
        "es": "Reabrir",
        "fr": "Réouvrir",
        "pt-BR": "Reabrir",
        "ru": "Открыть снова",
        "ja": "再開",
        "ko": "다시 열기",
        "zh-CN": "重新打开",
        "zh-TW": "重新開啟",
    },
    "tasks.cancelRun": {
        "de": "Lauf stoppen",
        "es": "Detener la ejecución",
        "fr": "Arrêter l'exécution",
        "pt-BR": "Parar a execução",
        "ru": "Остановить запуск",
        "ja": "実行を停止",
        "ko": "실행 중지",
        "zh-CN": "停止运行",
        "zh-TW": "停止執行",
    },
    "tasks.untitled": {
        "de": "Ohne Titel",
        "es": "Sin título",
        "fr": "Sans titre",
        "pt-BR": "Sem título",
        "ru": "Без названия",
        "ja": "無題",
        "ko": "제목 없음",
        "zh-CN": "未命名",
        "zh-TW": "未命名",
    },
    "tasks.delete": {
        "de": "Löschen",
        "es": "Eliminar",
        "fr": "Supprimer",
        "pt-BR": "Excluir",
        "ru": "Удалить",
        "ja": "削除",
        "ko": "삭제",
        "zh-CN": "删除",
        "zh-TW": "刪除",
    },
    "tasks.needsReview": {
        "de": "Prüfbereit",
        "es": "Listo para revisar",
        "fr": "À vérifier",
        "pt-BR": "Pronto para revisar",
        "ru": "Готово к проверке",
        "ja": "確認待ち",
        "ko": "확인 필요",
        "zh-CN": "待检查",
        "zh-TW": "待檢查",
    },
    "tasks.review": {
        "de": "Prüfbereit",
        "es": "Listo para revisar",
        "fr": "À vérifier",
        "pt-BR": "Pronto para revisar",
        "ru": "Готово к проверке",
        "ja": "確認待ち",
        "ko": "확인 필요",
        "zh-CN": "待检查",
        "zh-TW": "待檢查",
    },
    "tasks.lastRun": {
        "de": "letzter Lauf {{status}} {{when}}",
        "es": "última ejecución {{status}} {{when}}",
        "fr": "dernière exécution {{status}} {{when}}",
        "pt-BR": "última execução {{status}} {{when}}",
        "ru": "последний запуск {{status}} {{when}}",
        "ja": "前回の実行 {{status}} {{when}}",
        "ko": "마지막 실행 {{status}} {{when}}",
        "zh-CN": "上次运行 {{status}} {{when}}",
        "zh-TW": "上次執行 {{status}} {{when}}",
    },
    "tasks.reminder": {
        "de": "Erinnerung",
        "es": "Recordatorio",
        "fr": "Rappel",
        "pt-BR": "Lembrete",
        "ru": "Напоминание",
        "ja": "リマインダー",
        "ko": "알림",
        "zh-CN": "提醒",
        "zh-TW": "提醒",
    },
    "tasks.deferred": {
        "de": "Aufgeschoben",
        "es": "Aplazada",
        "fr": "Reportée",
        "pt-BR": "Adiada",
        "ru": "Отложено",
        "ja": "延期",
        "ko": "연기됨",
        "zh-CN": "已推迟",
        "zh-TW": "已延後",
    },
    "tasks.failedToReview": {
        "de": "Aufgabe konnte nicht aktualisiert werden",
        "es": "No se pudo actualizar la tarea",
        "fr": "Impossible de mettre à jour la tâche",
        "pt-BR": "Falha ao atualizar a tarefa",
        "ru": "Не удалось обновить задачу",
        "ja": "タスクを更新できませんでした",
        "ko": "작업을 업데이트하지 못했습니다",
        "zh-CN": "更新任务失败",
        "zh-TW": "更新任務失敗",
    },
    "tasks.failedToCancel": {
        "de": "Aufgabe konnte nicht gestoppt werden",
        "es": "No se pudo detener la tarea",
        "fr": "Impossible d'arrêter la tâche",
        "pt-BR": "Falha ao parar a tarefa",
        "ru": "Не удалось остановить задачу",
        "ja": "タスクを停止できませんでした",
        "ko": "작업을 중지하지 못했습니다",
        "zh-CN": "停止任务失败",
        "zh-TW": "停止任務失敗",
    },
}

#: English is the source of truth, written out here so `--check` can verify it.
ENGLISH: dict[str, str] = {
    "tasks.title": "Tasks",
    "tasks.manage": "Tasks",
    "tasks.open": "Open",
    "tasks.done": "Done",
    "tasks.forModel": "A model does it",
    "tasks.untitled": "Untitled",
    "tasks.delete": "Delete",
    "tasks.needsReview": "Ready to check",
    "tasks.review": "Ready to check",
    "tasks.lastRun": "last run {{status}} {{when}}",
    "tasks.reminder": "Reminder",
    "tasks.deferred": "Deferred",
    "tasks.failedToReview": "Failed to update the task",
    "tasks.failedToCancel": "Failed to stop the task",
    "tasks.approve": "Approve",
    "tasks.reopen": "Reopen",
    "tasks.cancelRun": "Stop the run",
}


def values(locale: str, existing: dict[str, str]) -> dict[str, str]:
    """The full `tasks.*` block: new wording, plus wording copied from this file."""
    out: dict[str, str] = {}
    for key in KEYS:
        if key in COPY:
            source = COPY[key]
            if source not in existing:
                raise SystemExit(f"{locale}: no source string {source} for {key}")
            out[key] = existing[source]
        elif locale == "en":
            out[key] = ENGLISH[key]
        elif key in TRANSLATIONS:
            out[key] = TRANSLATIONS[key][locale]
        else:  # pragma: no cover - a gap in the two tables above
            raise SystemExit(f"{locale}: no translation for {key}")
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

    # Only the keys this file lacks: re-running after a later addition must not
    # restate the block that is already there (JSON would keep the last of each
    # duplicate pair, hiding the drift instead of failing on it).
    pairs = {k: v for k, v in values(locale, existing).items() if k in missing}
    # Append before the closing brace: the last existing entry already ends with
    # a comma in these files, but tolerate one that does not.
    end = text.rstrip()
    assert end.endswith("}"), path.name
    head = end[:-1]
    comma = "" if head.rstrip().endswith(",") else ","
    body = block(pairs).rstrip()
    assert body.endswith(","), path.name
    patched = f"{head}{comma}\n{body[:-1]}\n}}\n"

    data = json.loads(patched)  # must stay valid JSON
    for key, want in pairs.items():
        assert data[key] == want, f"{path.name}: {key}"
    assert len(data) == len(existing) + len(missing), path.name
    path.write_text(patched, encoding="utf-8")
    return f"{path.name}: added {len(missing)} keys"


def main() -> int:
    check = "--check" in sys.argv
    out = []
    for locale in ["en", *TRANSLATIONS["tasks.title"]]:
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
