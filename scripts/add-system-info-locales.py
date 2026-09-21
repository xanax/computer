#!/usr/bin/env python3
"""Add the kill/restart keys for the System-info dialog to every locale.

The en.json block added by the System-info work (system.kill … system.restartFailed)
is missing from the nine translated locales, which makes those UIs fall back to
English mid-dialog. This inserts the translated block directly after
`system.load` — the same place it sits in en.json — preserving each file's own
formatting (tab indent, `"key": "value",`) rather than re-serialising the JSON.

Usage:  .venv/bin/python scripts/add-system-info-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

# key order taken from en.json
KEYS = [
    "system.kill",
    "system.killForce",
    "system.killForceHint",
    "system.killTitle",
    "system.killConfirm",
    "system.cptrRow",
    "system.restart",
    "system.restartHint",
    "system.restartConfirm",
    "system.restartStopping",
    "system.restartWaiting",
    "system.restartSlow",
    "system.restartReload",
    "system.restartFailed",
]

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        "system.kill": "Beenden",
        "system.killForce": "Erzwingen",
        "system.killForceHint": "SIGKILL statt SIGTERM senden",
        "system.killTitle": "{{name}} beenden (PID {{pid}})",
        "system.killConfirm": "{{name}} beenden?",
        "system.cptrRow": "Server",
        "system.restart": "Neu starten",
        "system.restartHint": "cptr-Server PID {{pid}}",
        "system.restartConfirm": "Server neu starten? Der laufende Durchgang wird unterbrochen.",
        "system.restartStopping": "Neustart — der alte Server wird beendet…",
        "system.restartWaiting": "Neustart — warte auf den Server…",
        "system.restartSlow": "Der Server ist noch nicht zurück.",
        "system.restartReload": "Neu laden",
        "system.restartFailed": "Neustart konnte nicht geplant werden: {{message}}",
    },
    "fr": {
        "system.kill": "Arrêter",
        "system.killForce": "Forcer",
        "system.killForceHint": "Envoyer SIGKILL au lieu de SIGTERM",
        "system.killTitle": "Arrêter {{name}} (PID {{pid}})",
        "system.killConfirm": "Arrêter {{name}} ?",
        "system.cptrRow": "serveur",
        "system.restart": "Redémarrer",
        "system.restartHint": "PID du serveur cptr {{pid}}",
        "system.restartConfirm": "Redémarrer le serveur ? Le tour en cours sera interrompu.",
        "system.restartStopping": "Redémarrage — arrêt de l’ancien serveur…",
        "system.restartWaiting": "Redémarrage — en attente du retour du serveur…",
        "system.restartSlow": "Le serveur n’est pas encore revenu.",
        "system.restartReload": "Recharger",
        "system.restartFailed": "Impossible de planifier le redémarrage : {{message}}",
    },
    "es": {
        "system.kill": "Terminar",
        "system.killForce": "Forzar",
        "system.killForceHint": "Enviar SIGKILL en lugar de SIGTERM",
        "system.killTitle": "Terminar {{name}} (PID {{pid}})",
        "system.killConfirm": "¿Terminar {{name}}?",
        "system.cptrRow": "servidor",
        "system.restart": "Reiniciar",
        "system.restartHint": "PID del servidor cptr {{pid}}",
        "system.restartConfirm": "¿Reiniciar el servidor? El turno en curso se interrumpirá.",
        "system.restartStopping": "Reiniciando — deteniendo el servidor anterior…",
        "system.restartWaiting": "Reiniciando — esperando a que vuelva el servidor…",
        "system.restartSlow": "El servidor aún no ha vuelto.",
        "system.restartReload": "Recargar",
        "system.restartFailed": "No se pudo programar el reinicio: {{message}}",
    },
    "pt-BR": {
        "system.kill": "Encerrar",
        "system.killForce": "Forçar",
        "system.killForceHint": "Enviar SIGKILL em vez de SIGTERM",
        "system.killTitle": "Encerrar {{name}} (PID {{pid}})",
        "system.killConfirm": "Encerrar {{name}}?",
        "system.cptrRow": "servidor",
        "system.restart": "Reiniciar",
        "system.restartHint": "PID do servidor cptr {{pid}}",
        "system.restartConfirm": "Reiniciar o servidor? O turno em andamento será interrompido.",
        "system.restartStopping": "Reiniciando — encerrando o servidor anterior…",
        "system.restartWaiting": "Reiniciando — aguardando o servidor voltar…",
        "system.restartSlow": "O servidor ainda não voltou.",
        "system.restartReload": "Recarregar",
        "system.restartFailed": "Não foi possível agendar a reinicialização: {{message}}",
    },
    "ru": {
        "system.kill": "Завершить",
        "system.killForce": "Принудительно",
        "system.killForceHint": "Отправить SIGKILL вместо SIGTERM",
        "system.killTitle": "Завершить {{name}} (PID {{pid}})",
        "system.killConfirm": "Завершить {{name}}?",
        "system.cptrRow": "сервер",
        "system.restart": "Перезапустить",
        "system.restartHint": "PID сервера cptr {{pid}}",
        "system.restartConfirm": "Перезапустить сервер? Текущий ход будет прерван.",
        "system.restartStopping": "Перезапуск — останавливаем старый сервер…",
        "system.restartWaiting": "Перезапуск — ждём возвращения сервера…",
        "system.restartSlow": "Сервер ещё не вернулся.",
        "system.restartReload": "Перезагрузить",
        "system.restartFailed": "Не удалось запланировать перезапуск: {{message}}",
    },
    "ja": {
        "system.kill": "終了",
        "system.killForce": "強制終了",
        "system.killForceHint": "SIGTERM の代わりに SIGKILL を送信",
        "system.killTitle": "{{name}} を終了 (PID {{pid}})",
        "system.killConfirm": "{{name}} を終了しますか？",
        "system.cptrRow": "サーバー",
        "system.restart": "再起動",
        "system.restartHint": "cptr サーバー PID {{pid}}",
        "system.restartConfirm": "サーバーを再起動しますか？進行中のターンは中断されます。",
        "system.restartStopping": "再起動中 — 旧サーバーを停止しています…",
        "system.restartWaiting": "再起動中 — サーバーの復帰を待っています…",
        "system.restartSlow": "サーバーはまだ復帰していません。",
        "system.restartReload": "再読み込み",
        "system.restartFailed": "再起動を予約できませんでした: {{message}}",
    },
    "ko": {
        "system.kill": "종료",
        "system.killForce": "강제 종료",
        "system.killForceHint": "SIGTERM 대신 SIGKILL 전송",
        "system.killTitle": "{{name}} 종료 (PID {{pid}})",
        "system.killConfirm": "{{name}} 프로세스를 종료할까요?",
        "system.cptrRow": "서버",
        "system.restart": "재시작",
        "system.restartHint": "cptr 서버 PID {{pid}}",
        "system.restartConfirm": "서버를 재시작할까요? 진행 중인 턴이 중단됩니다.",
        "system.restartStopping": "재시작 중 — 이전 서버를 중지하는 중…",
        "system.restartWaiting": "재시작 중 — 서버가 돌아오기를 기다리는 중…",
        "system.restartSlow": "서버가 아직 돌아오지 않았습니다.",
        "system.restartReload": "새로 고침",
        "system.restartFailed": "재시작을 예약할 수 없습니다: {{message}}",
    },
    "zh-CN": {
        "system.kill": "结束",
        "system.killForce": "强制结束",
        "system.killForceHint": "发送 SIGKILL 而不是 SIGTERM",
        "system.killTitle": "结束 {{name}}（PID {{pid}}）",
        "system.killConfirm": "要结束 {{name}} 吗？",
        "system.cptrRow": "服务器",
        "system.restart": "重启",
        "system.restartHint": "cptr 服务器 PID {{pid}}",
        "system.restartConfirm": "要重启服务器吗？正在进行的回合将被中断。",
        "system.restartStopping": "正在重启 — 正在停止旧服务器…",
        "system.restartWaiting": "正在重启 — 正在等待服务器恢复…",
        "system.restartSlow": "服务器尚未恢复。",
        "system.restartReload": "重新加载",
        "system.restartFailed": "无法安排重启：{{message}}",
    },
    "zh-TW": {
        "system.kill": "結束",
        "system.killForce": "強制結束",
        "system.killForceHint": "傳送 SIGKILL 而非 SIGTERM",
        "system.killTitle": "結束 {{name}}（PID {{pid}}）",
        "system.killConfirm": "要結束 {{name}} 嗎？",
        "system.cptrRow": "伺服器",
        "system.restart": "重新啟動",
        "system.restartHint": "cptr 伺服器 PID {{pid}}",
        "system.restartConfirm": "要重新啟動伺服器嗎？進行中的回合將被中斷。",
        "system.restartStopping": "正在重新啟動 — 正在停止舊伺服器…",
        "system.restartWaiting": "正在重新啟動 — 等待伺服器恢復…",
        "system.restartSlow": "伺服器尚未恢復。",
        "system.restartReload": "重新載入",
        "system.restartFailed": "無法安排重新啟動：{{message}}",
    },
}


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
    anchor = '\t"system.load": '
    idx = text.find(anchor)
    if idx < 0:
        return f"{path.name}: ERROR no system.load anchor"
    end = text.index("\n", idx) + 1  # insert after the whole system.load line
    if check:
        return f"{path.name}: MISSING {len(missing)} keys"
    patched = text[:end] + block(locale) + "\n" + text[end:]
    data = json.loads(patched)  # must stay valid JSON
    assert all(data[k] == TRANSLATIONS[locale][k] for k in KEYS), path.name
    # the rewritten file must differ only by the inserted lines
    assert patched.replace(block(locale) + "\n", "", 1) == text, path.name
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
