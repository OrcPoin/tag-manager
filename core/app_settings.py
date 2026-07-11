"""Сохранение пользовательских настроек между сессиями (settings.json).

Стримлит держит настройки в session_state, который обнуляется при перезапуске
приложения (и при F5). Чтобы не переставлять галки и слайдеры каждый раз, мы
сохраняем «липкие» настройки в JSON рядом с приложением и подгружаем их при
старте. Промпты/пресеты сюда НЕ входят — у них своя система (presets.json).
"""

from __future__ import annotations

import json
import os

from config import SETTINGS_FILE

# Ключи, которые персистятся. Значения по умолчанию берутся из config при старте
# (см. app.init_state) — здесь только перечень того, что сохраняем/грузим.
PERSISTED_KEYS = (
    "api_url",
    "model",
    "temperature",
    "max_tokens",
    "top_p",
    "timeout",
    "auto_retry",
    "manual_review",
    "disable_thinking",
    "trigger_word",
)


def load_settings() -> dict:
    """Прочитать сохранённые настройки (пустой dict, если файла нет/битый)."""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            # Возвращаем только известные ключи — на случай старого/чужого файла.
            return {k: data[k] for k in PERSISTED_KEYS if k in data}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_settings(values: dict) -> None:
    """Записать настройки в settings.json (только PERSISTED_KEYS)."""
    data = {k: values[k] for k in PERSISTED_KEYS if k in values}
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
