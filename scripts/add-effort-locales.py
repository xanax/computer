"""Add the effortSelector.* keys to every locale.

Mirrors scripts/add-*-locales.py in this repo: keys are flat, sorted nowhere in
particular, and appended to the end of the object. Values are kept in the same
place across locales so a missing translation is obvious in a diff.
"""

from __future__ import annotations

import json
from pathlib import Path

LOCALES = (
    Path(__file__).resolve().parent.parent / "cptr/frontend/src/lib/i18n/locales"
)

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "effortSelector.title": "Reasoning effort",
        "effortSelector.default": "Default",
        "effortSelector.minimal": "Minimal",
        "effortSelector.low": "Low",
        "effortSelector.medium": "Medium",
        "effortSelector.high": "High",
        "models.effortLevels": "Reasoning effort",
        "models.effortLevelsAuto": "Automatic",
        "models.effortLevelsHint":
            "Automatic lets cptr decide from the model name. Select no levels to remove the effort selector from the composer.",
    },
    "de": {
        "effortSelector.title": "Denkaufwand",
        "effortSelector.default": "Standard",
        "effortSelector.minimal": "Minimal",
        "effortSelector.low": "Niedrig",
        "effortSelector.medium": "Mittel",
        "effortSelector.high": "Hoch",
        "models.effortLevels": "Denkaufwand",
        "models.effortLevelsAuto": "Automatisch",
        "models.effortLevelsHint":
            "Automatisch lässt cptr anhand des Modellnamens entscheiden. Wähle keine Stufe aus, um die Auswahl im Eingabefeld zu entfernen.",
    },
    "es": {
        "effortSelector.title": "Esfuerzo de razonamiento",
        "effortSelector.default": "Predeterminado",
        "effortSelector.minimal": "Mínimo",
        "effortSelector.low": "Bajo",
        "effortSelector.medium": "Medio",
        "effortSelector.high": "Alto",
        "models.effortLevels": "Esfuerzo de razonamiento",
        "models.effortLevelsAuto": "Automático",
        "models.effortLevelsHint":
            "Automático deja que cptr decida según el nombre del modelo. No selecciones ningún nivel para quitar el selector de esfuerzo del compositor.",
    },
    "fr": {
        "effortSelector.title": "Effort de raisonnement",
        "effortSelector.default": "Par défaut",
        "effortSelector.minimal": "Minimal",
        "effortSelector.low": "Faible",
        "effortSelector.medium": "Moyen",
        "effortSelector.high": "Élevé",
        "models.effortLevels": "Effort de raisonnement",
        "models.effortLevelsAuto": "Automatique",
        "models.effortLevelsHint":
            "En mode automatique, cptr décide d'après le nom du modèle. Ne sélectionnez aucun niveau pour retirer le sélecteur d'effort de la zone de saisie.",
    },
    "ja": {
        "effortSelector.title": "推論の深さ",
        "effortSelector.default": "デフォルト",
        "effortSelector.minimal": "最小",
        "effortSelector.low": "低",
        "effortSelector.medium": "中",
        "effortSelector.high": "高",
        "models.effortLevels": "推論の深さ",
        "models.effortLevelsAuto": "自動",
        "models.effortLevelsHint":
            "自動にすると、cptr がモデル名から判断します。ステージを1つも選択しない場合は、入力欄から推論の深さの選択を削除します。",
    },
    "ko": {
        "effortSelector.title": "추론 강도",
        "effortSelector.default": "기본값",
        "effortSelector.minimal": "최소",
        "effortSelector.low": "낮음",
        "effortSelector.medium": "중간",
        "effortSelector.high": "높음",
        "models.effortLevels": "추론 강도",
        "models.effortLevelsAuto": "자동",
        "models.effortLevelsHint":
            "자동으로 두면 cptr가 모델 이름으로 판단합니다. 아무 단계도 선택하지 않으면 입력창에서 추론 강도 선택기가 사라집니다.",
    },
    "pt-BR": {
        "effortSelector.title": "Esforço de raciocínio",
        "effortSelector.default": "Padrão",
        "effortSelector.minimal": "Mínimo",
        "effortSelector.low": "Baixo",
        "effortSelector.medium": "Médio",
        "effortSelector.high": "Alto",
        "models.effortLevels": "Esforço de raciocínio",
        "models.effortLevelsAuto": "Automático",
        "models.effortLevelsHint":
            "Automático deixa o cptr decidir pelo nome do modelo. Não selecione nenhum nível para remover o seletor de esforço do campo de mensagem.",
    },
    "ru": {
        "effortSelector.title": "Глубина рассуждений",
        "effortSelector.default": "По умолчанию",
        "effortSelector.minimal": "Минимум",
        "effortSelector.low": "Низкий",
        "effortSelector.medium": "Средний",
        "effortSelector.high": "Высокий",
        "models.effortLevels": "Глубина рассуждений",
        "models.effortLevelsAuto": "Автоматически",
        "models.effortLevelsHint":
            "В автоматическом режиме cptr определяет уровень по названию модели. Не выбирайте ни одного уровня, чтобы убрать выбор глубины из поля ввода.",
    },
    "zh-CN": {
        "effortSelector.title": "推理强度",
        "effortSelector.default": "默认",
        "effortSelector.minimal": "最低",
        "effortSelector.low": "低",
        "effortSelector.medium": "中",
        "effortSelector.high": "高",
        "models.effortLevels": "推理强度",
        "models.effortLevelsAuto": "自动",
        "models.effortLevelsHint":
            "设为自动时，cptr 会根据模型名称判断。不选择任何级别，即可从输入框移除推理强度选择器。",
    },
    "zh-TW": {
        "effortSelector.title": "推理強度",
        "effortSelector.default": "預設",
        "effortSelector.minimal": "最低",
        "effortSelector.low": "低",
        "effortSelector.medium": "中",
        "effortSelector.high": "高",
        "models.effortLevels": "推理強度",
        "models.effortLevelsAuto": "自動",
        "models.effortLevelsHint":
            "設為自動時，cptr 會依模型名稱判斷。不選擇任何級別，即可從輸入框移除推理強度選擇器。",
    },
}

EN = TRANSLATIONS["en"]


def main() -> None:
    for locale, entries in TRANSLATIONS.items():
        path = LOCALES / f"{locale}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        added = [key for key in EN if key not in data]
        data.update(entries)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8"
        )
        print(f"{locale}: +{len(added)} keys (total {len(data)})")


if __name__ == "__main__":
    main()