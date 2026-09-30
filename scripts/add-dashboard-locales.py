#!/usr/bin/env python3
"""Translate the `dashboard.*` keys (the workspace dashboard) into every locale.

The dashboard is a fork addition: its 40 `dashboard.*` strings exist only in
en.json, so every other locale falls back to English mid-page — including the
task rows the board shares with the Tasks tab (`dashboard.job*`). This adds the
whole block to the nine translated locales, copying wording that already exists
in the same file (`common.add`, `search.recentChats`, `tasks.approve`, …) and
translating the rest by hand.

The block is appended at the end of each file, so a patched locale differs only
by the inserted lines.

Usage:  .venv/bin/python scripts/add-dashboard-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

#: Order of the inserted block (mirrors en.json).
KEYS = [
    "dashboard.title",
    "dashboard.backToWorkspace",
    "dashboard.newChat",
    "dashboard.manageScheduled",
    "dashboard.upcoming",
    "dashboard.noUpcoming",
    "dashboard.createScheduledHint",
    "dashboard.recentChats",
    "dashboard.noChats",
    "dashboard.now",
    "dashboard.minutes_one",
    "dashboard.minutes_other",
    "dashboard.hours_one",
    "dashboard.hours_other",
    "dashboard.days_one",
    "dashboard.days_other",
    "dashboard.todos",
    "dashboard.noTodos",
    "dashboard.addTodoPlaceholder",
    "dashboard.addTodo",
    "dashboard.pendingVerification",
    "dashboard.noPendingRequests",
    "dashboard.approve",
    "dashboard.reject",
    "dashboard.complete",
    "dashboard.reopen",
    "dashboard.remove",
    "dashboard.jobQueued",
    "dashboard.jobScheduled",
    "dashboard.jobRunning",
    "dashboard.jobReady",
    "dashboard.jobBlocked",
    "dashboard.jobFailed",
    "dashboard.jobStopped",
    "dashboard.jobAttempt_one",
    "dashboard.jobAttempt_other",
    "dashboard.jobStop",
    "dashboard.jobClearRun",
    "dashboard.jobDismissRun",
    "dashboard.jobOpenRun",
]

#: dashboard key → a key this locale already has for the same idea.
COPY = {
    "dashboard.newChat": "bar.newChat",
    "dashboard.recentChats": "search.recentChats",
    "dashboard.addTodo": "common.add",
    "dashboard.approve": "tasks.approve",
    "dashboard.reject": "common.reject",
    "dashboard.reopen": "tasks.reopen",
    "dashboard.remove": "common.remove",
    "dashboard.jobStop": "tasks.cancelRun",
}

#: The rest, one row per key, one column per locale.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "dashboard.title": {
        "de": "Dashboard",
        "es": "Panel",
        "fr": "Tableau de bord",
        "pt-BR": "Painel",
        "ru": "Панель",
        "ja": "ダッシュボード",
        "ko": "대시보드",
        "zh-CN": "面板",
        "zh-TW": "面板",
    },
    "dashboard.backToWorkspace": {
        "de": "Zurück zum Arbeitsbereich",
        "es": "Volver al espacio de trabajo",
        "fr": "Retour à l’espace de travail",
        "pt-BR": "Voltar ao espaço de trabalho",
        "ru": "Назад в рабочую область",
        "ja": "ワークスペースに戻る",
        "ko": "워크스페이스로 돌아가기",
        "zh-CN": "返回工作区",
        "zh-TW": "返回工作區",
    },
    "dashboard.manageScheduled": {
        "de": "Geplante Aufgaben verwalten",
        "es": "Gestionar tareas programadas",
        "fr": "Gérer les tâches planifiées",
        "pt-BR": "Gerenciar tarefas agendadas",
        "ru": "Управление задачами по расписанию",
        "ja": "予定されたタスクを管理",
        "ko": "예약된 작업 관리",
        "zh-CN": "管理计划任务",
        "zh-TW": "管理排程任務",
    },
    "dashboard.upcoming": {
        "de": "Anstehende geplante Aufgaben",
        "es": "Próximas tareas programadas",
        "fr": "Tâches planifiées à venir",
        "pt-BR": "Próximas tarefas agendadas",
        "ru": "Ближайшие задачи по расписанию",
        "ja": "今後の予定タスク",
        "ko": "예정된 작업",
        "zh-CN": "即将开始的任务",
        "zh-TW": "即將開始的任務",
    },
    "dashboard.noUpcoming": {
        "de": "Keine anstehenden geplanten Aufgaben",
        "es": "No hay próximas tareas programadas",
        "fr": "Aucune tâche planifiée à venir",
        "pt-BR": "Nenhuma tarefa agendada por vir",
        "ru": "Нет ближайших задач по расписанию",
        "ja": "今後の予定タスクはありません",
        "ko": "예정된 작업이 없습니다",
        "zh-CN": "没有即将开始的任务",
        "zh-TW": "沒有即將開始的任務",
    },
    "dashboard.createScheduledHint": {
        "de": "Erstelle eine, um sie hier zu sehen.",
        "es": "Crea una para verla aquí.",
        "fr": "Créez-en une pour la voir apparaître ici.",
        "pt-BR": "Crie uma para vê-la aqui.",
        "ru": "Создайте её, чтобы увидеть здесь.",
        "ja": "作成するとここに表示されます。",
        "ko": "하나 만들면 여기에 표시됩니다.",
        "zh-CN": "创建一个即可在此显示。",
        "zh-TW": "建立一個即可在此顯示。",
    },
    "dashboard.noChats": {
        "de": "Noch keine Chats",
        "es": "Aún no hay chats",
        "fr": "Aucune conversation",
        "pt-BR": "Nenhum chat ainda",
        "ru": "Чатов пока нет",
        "ja": "チャットはまだありません",
        "ko": "아직 채팅이 없습니다",
        "zh-CN": "还没有聊天",
        "zh-TW": "還沒有對話",
    },
    "dashboard.now": {
        "de": "jetzt",
        "es": "ahora",
        "fr": "maintenant",
        "pt-BR": "agora",
        "ru": "сейчас",
        "ja": "今",
        "ko": "지금",
        "zh-CN": "现在",
        "zh-TW": "現在",
    },
    "dashboard.minutes_one": {
        "de": "in {{count}} Min.",
        "es": "en {{count}} min",
        "fr": "dans {{count}} min",
        "pt-BR": "em {{count}} min",
        "ru": "через {{count}} мин",
        "ja": "{{count}} 分後",
        "ko": "{{count}}분 후",
        "zh-CN": "{{count}} 分钟后",
        "zh-TW": "{{count}} 分鐘後",
    },
    "dashboard.minutes_other": {
        "de": "in {{count}} Min.",
        "es": "en {{count}} min",
        "fr": "dans {{count}} min",
        "pt-BR": "em {{count}} min",
        "ru": "через {{count}} мин",
        "ja": "{{count}} 分後",
        "ko": "{{count}}분 후",
        "zh-CN": "{{count}} 分钟后",
        "zh-TW": "{{count}} 分鐘後",
    },
    "dashboard.hours_one": {
        "de": "in {{count}} Std.",
        "es": "en {{count}} h",
        "fr": "dans {{count}} h",
        "pt-BR": "em {{count}} h",
        "ru": "через {{count}} ч",
        "ja": "{{count}} 時間後",
        "ko": "{{count}}시간 후",
        "zh-CN": "{{count}} 小时后",
        "zh-TW": "{{count}} 小時後",
    },
    "dashboard.hours_other": {
        "de": "in {{count}} Std.",
        "es": "en {{count}} h",
        "fr": "dans {{count}} h",
        "pt-BR": "em {{count}} h",
        "ru": "через {{count}} ч",
        "ja": "{{count}} 時間後",
        "ko": "{{count}}시간 후",
        "zh-CN": "{{count}} 小时后",
        "zh-TW": "{{count}} 小時後",
    },
    "dashboard.days_one": {
        "de": "in {{count}} Tag",
        "es": "en {{count}} día",
        "fr": "dans {{count}} jour",
        "pt-BR": "em {{count}} dia",
        "ru": "через {{count}} день",
        "ja": "{{count}} 日後",
        "ko": "{{count}}일 후",
        "zh-CN": "{{count}} 天后",
        "zh-TW": "{{count}} 天後",
    },
    "dashboard.days_other": {
        "de": "in {{count}} Tagen",
        "es": "en {{count}} días",
        "fr": "dans {{count}} jours",
        "pt-BR": "em {{count}} dias",
        "ru": "через {{count}} дней",
        "ja": "{{count}} 日後",
        "ko": "{{count}}일 후",
        "zh-CN": "{{count}} 天后",
        "zh-TW": "{{count}} 天後",
    },
    "dashboard.todos": {
        "de": "To-dos",
        "es": "Pendientes",
        "fr": "À faire",
        "pt-BR": "Afazeres",
        "ru": "Дела",
        "ja": "ToDo",
        "ko": "할 일",
        "zh-CN": "待办",
        "zh-TW": "待辦",
    },
    "dashboard.noTodos": {
        "de": "Noch keine To-dos",
        "es": "Sin pendientes",
        "fr": "Rien à faire",
        "pt-BR": "Nada a fazer",
        "ru": "Дел пока нет",
        "ja": "ToDo はまだありません",
        "ko": "할 일이 없습니다",
        "zh-CN": "没有待办",
        "zh-TW": "沒有待辦",
    },
    "dashboard.addTodoPlaceholder": {
        "de": "To-do hinzufügen…",
        "es": "Añadir un pendiente…",
        "fr": "Ajouter une tâche…",
        "pt-BR": "Adicionar um afazer…",
        "ru": "Добавить дело…",
        "ja": "ToDo を追加…",
        "ko": "할 일 추가…",
        "zh-CN": "添加待办…",
        "zh-TW": "新增待辦…",
    },
    "dashboard.pendingVerification": {
        "de": "Warten auf Prüfung",
        "es": "Pendiente de revisión",
        "fr": "En attente de vérification",
        "pt-BR": "Aguardando verificação",
        "ru": "Ожидает проверки",
        "ja": "確認待ち",
        "ko": "확인 대기 중",
        "zh-CN": "等待确认",
        "zh-TW": "等待確認",
    },
    "dashboard.noPendingRequests": {
        "de": "Nichts zu prüfen",
        "es": "Nada pendiente de revisión",
        "fr": "Rien à vérifier",
        "pt-BR": "Nada aguardando verificação",
        "ru": "Нечего проверять",
        "ja": "確認待ちはありません",
        "ko": "확인할 항목이 없습니다",
        "zh-CN": "没有待确认的请求",
        "zh-TW": "沒有待確認的請求",
    },
    "dashboard.complete": {
        "de": "Erledigen",
        "es": "Completar",
        "fr": "Terminer",
        "pt-BR": "Concluir",
        "ru": "Завершить",
        "ja": "完了にする",
        "ko": "완료",
        "zh-CN": "完成",
        "zh-TW": "完成",
    },
    "dashboard.jobQueued": {
        "de": "wartet auf Ausführung",
        "es": "en espera",
        "fr": "en attente d’exécution",
        "pt-BR": "aguardando execução",
        "ru": "ожидает запуска",
        "ja": "実行待ち",
        "ko": "실행 대기 중",
        "zh-CN": "等待运行",
        "zh-TW": "等待執行",
    },
    "dashboard.jobScheduled": {
        "de": "geplant",
        "es": "programada",
        "fr": "planifiée",
        "pt-BR": "agendada",
        "ru": "по расписанию",
        "ja": "予定",
        "ko": "예약됨",
        "zh-CN": "已计划",
        "zh-TW": "已排程",
    },
    "dashboard.jobRunning": {
        "de": "läuft gerade",
        "es": "en ejecución",
        "fr": "en cours",
        "pt-BR": "em execução",
        "ru": "выполняется",
        "ja": "実行中",
        "ko": "실행 중",
        "zh-CN": "正在运行",
        "zh-TW": "正在執行",
    },
    "dashboard.jobReady": {
        "de": "bereit zur Prüfung",
        "es": "lista para revisar",
        "fr": "prête à vérifier",
        "pt-BR": "pronta para revisar",
        "ru": "готова к проверке",
        "ja": "確認できます",
        "ko": "확인 가능",
        "zh-CN": "可以检查",
        "zh-TW": "可以檢查",
    },
    "dashboard.jobBlocked": {
        "de": "blockiert",
        "es": "bloqueada",
        "fr": "bloquée",
        "pt-BR": "bloqueada",
        "ru": "заблокирована",
        "ja": "ブロック中",
        "ko": "차단됨",
        "zh-CN": "已阻塞",
        "zh-TW": "已封鎖",
    },
    "dashboard.jobFailed": {
        "de": "fehlgeschlagen",
        "es": "fallida",
        "fr": "échouée",
        "pt-BR": "falhou",
        "ru": "сбой",
        "ja": "失敗",
        "ko": "실패",
        "zh-CN": "失败",
        "zh-TW": "失敗",
    },
    "dashboard.jobStopped": {
        "de": "gestoppt",
        "es": "detenida",
        "fr": "arrêtée",
        "pt-BR": "parada",
        "ru": "остановлена",
        "ja": "停止",
        "ko": "중지됨",
        "zh-CN": "已停止",
        "zh-TW": "已停止",
    },
    "dashboard.jobAttempt_one": {
        "de": "Versuch {{count}}",
        "es": "intento {{count}}",
        "fr": "tentative {{count}}",
        "pt-BR": "tentativa {{count}}",
        "ru": "попытка {{count}}",
        "ja": "{{count}} 回目",
        "ko": "{{count}}번째 시도",
        "zh-CN": "第 {{count}} 次",
        "zh-TW": "第 {{count}} 次",
    },
    "dashboard.jobAttempt_other": {
        "de": "Versuche {{count}}",
        "es": "intentos {{count}}",
        "fr": "tentatives {{count}}",
        "pt-BR": "tentativas {{count}}",
        "ru": "попыток {{count}}",
        "ja": "{{count}} 回目",
        "ko": "{{count}}번째 시도",
        "zh-CN": "第 {{count}} 次",
        "zh-TW": "第 {{count}} 次",
    },
    "dashboard.jobClearRun": {
        "de": "Geplanten Lauf löschen",
        "es": "Borrar la ejecución programada",
        "fr": "Effacer l’exécution planifiée",
        "pt-BR": "Limpar a execução agendada",
        "ru": "Очистить запуск по расписанию",
        "ja": "予定された実行を消去",
        "ko": "예약된 실행 지우기",
        "zh-CN": "清除计划运行",
        "zh-TW": "清除排程執行",
    },
    "dashboard.jobDismissRun": {
        "de": "Diesen beendeten Lauf ausblenden",
        "es": "Descartar esta ejecución terminada",
        "fr": "Masquer cette exécution terminée",
        "pt-BR": "Descartar esta execução concluída",
        "ru": "Убрать завершённый запуск",
        "ja": "完了した実行を消す",
        "ko": "완료된 실행 지우기",
        "zh-CN": "清除这次已完成的运行",
        "zh-TW": "清除這次已完成的執行",
    },
    "dashboard.jobOpenRun": {
        "de": "Chat des Laufs öffnen",
        "es": "Abrir el chat de la ejecución",
        "fr": "Ouvrir la conversation de l’exécution",
        "pt-BR": "Abrir o chat da execução",
        "ru": "Открыть чат запуска",
        "ja": "実行のチャットを開く",
        "ko": "실행 채팅 열기",
        "zh-CN": "打开该运行的聊天",
        "zh-TW": "開啟該執行的對話",
    },
}


def values(locale: str, existing: dict[str, str]) -> dict[str, str]:
    """The full `dashboard.*` block: translated wording, copied where possible."""
    out: dict[str, str] = {}
    for key in KEYS:
        if key in COPY and COPY[key] in existing:
            out[key] = existing[COPY[key]]
        elif key in TRANSLATIONS and locale in TRANSLATIONS[key]:
            out[key] = TRANSLATIONS[key][locale]
        else:  # pragma: no cover - a gap in the two tables above
            raise SystemExit(f"{locale}: no value for {key}")
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

    pairs = values(locale, existing)
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
    locales = list(TRANSLATIONS["dashboard.title"])
    if not check:
        locales = ["en", *locales]  # en already has them; patch() then says "complete"
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
