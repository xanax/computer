#!/usr/bin/env python3
"""Translate the `general.profile*` keys (the global profile box) into every locale.

The global profile is a fork addition: the Settings → General box that holds the
one prompt sent at the start of *every* chat, in every workspace. Its five
strings exist only in English until this runs, so every other locale would show
English mid-page. The block is appended at the end of each file, so a patched
locale differs only by the inserted lines.

Usage:  .venv/bin/python scripts/add-profile-locales.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "cptr/frontend/src/lib/i18n/locales"

#: The block, in the order it reads on the page.
KEYS = [
    "general.profile",
    "general.profileDesc",
    "general.profilePlaceholder",
    "general.profileEmpty",
    "general.profileSaveFailed",
]

#: The profile box wording, one row per key, one column per locale.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "general.profile": {
        "en": "Profile",
        "de": "Profil",
        "es": "Perfil",
        "fr": "Profil",
        "pt-BR": "Perfil",
        "ru": "Профиль",
        "ja": "プロフィール",
        "ko": "프로필",
        "zh-CN": "个人资料",
        "zh-TW": "個人資料",
    },
    "general.profileDesc": {
        "en": "Sent at the start of every chat — in every workspace, and on the home page.",
        "de": "Wird am Anfang jedes Chats gesendet – in jedem Arbeitsbereich und auf der Startseite.",
        "es": "Se envía al inicio de cada chat, en todos los espacios y en la página de inicio.",
        "fr": "Envoyé au début de chaque conversation — dans chaque espace et sur la page d’accueil.",
        "pt-BR": "Enviado no início de cada chat — em todos os espaços e na página inicial.",
        "ru": "Отправляется в начале каждого чата — в любом пространстве и на главной странице.",
        "ja": "すべてのワークスペースとホーム画面で、各チャットの冒頭に送信されます。",
        "ko": "모든 워크스페이스와 홈 화면에서 모든 채팅 시작 부분에 전송됩니다.",
        "zh-CN": "在每个工作区以及主页的每次聊天开头发送。",
        "zh-TW": "在每個工作區以及首頁的每次對話開頭傳送。",
    },
    "general.profilePlaceholder": {
        "en": "How should chats treat you?",
        "de": "Wie sollen Chats mit dir umgehen?",
        "es": "¿Cómo deben tratarte los chats?",
        "fr": "Comment les conversations doivent-elles vous traiter ?",
        "pt-BR": "Como os chats devem tratar você?",
        "ru": "Как чаты должны с вами обращаться?",
        "ja": "チャットはあなたをどう扱うべき？",
        "ko": "채팅이 당신을 어떻게 대해야 할까요?",
        "zh-CN": "聊天应该如何对待你？",
        "zh-TW": "對話應該如何對待你？",
    },
    "general.profileEmpty": {
        "en": "No profile yet.",
        "de": "Noch kein Profil.",
        "es": "Aún no hay perfil.",
        "fr": "Pas encore de profil.",
        "pt-BR": "Ainda sem perfil.",
        "ru": "Профиля пока нет.",
        "ja": "プロフィールはまだありません。",
        "ko": "아직 프로필이 없습니다.",
        "zh-CN": "还没有个人资料。",
        "zh-TW": "還沒有個人資料。",
    },
    "general.profileSaveFailed": {
        "en": "Failed to save profile",
        "de": "Profil konnte nicht gespeichert werden",
        "es": "No se pudo guardar el perfil",
        "fr": "Échec de l’enregistrement du profil",
        "pt-BR": "Falha ao salvar o perfil",
        "ru": "Не удалось сохранить профиль",
        "ja": "プロフィールを保存できませんでした",
        "ko": "프로필을 저장하지 못했습니다",
        "zh-CN": "保存个人资料失败",
        "zh-TW": "儲存個人資料失敗",
    },
}


def values(locale: str) -> dict[str, str]:
    """The full profile block for one locale."""
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
    locales = list(TRANSLATIONS["general.profile"])
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
