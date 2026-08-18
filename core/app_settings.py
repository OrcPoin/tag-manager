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
    "backend_type",
    "api_url",
    "model",
    "llama_executable",
    "model_directory",
    "mmproj_directory",
    "llama_model",
    "llama_mmproj",
    "llama_host",
    "llama_port",
    "llama_api_prefix",
    "llama_startup_timeout",
    "llama_optimization_mode",
    "llama_reasoning_budget",
    "llama_cache_k",
    "llama_cache_v",
    "llama_flash_attn",
    "llama_load_mode",
    "llama_slots",
    "llama_threads",
    "llama_batch",
    "llama_ubatch",
    "llama_gpu_layers",
    "llama_fit_target",
    "llama_context_size",
    "temperature",
    "max_tokens",
    "top_p",
    "timeout",
    "auto_retry",
    "manual_review",
    "disable_thinking",
    "trigger_word",
    "notify_on_finish",
    "caption_edit_height",
    "folder",
    "recursive",
    "preset_name",
    "pipeline_mode",
    "pipeline_tagger_ids",
    "ui_theme",
    "desktop_keep_background",
    "desktop_autostart",
    "desktop_window_width",
    "desktop_window_height",
    "desktop_window_x",
    "desktop_window_y",
    "desktop_window_maximized",
)

# Ошибочное имя кратко использовалось в незавершённой версии настройки.
_KEY_MIGRATIONS = {"caption_editor_height": "caption_edit_height"}


def load_settings() -> dict:
    """Прочитать сохранённые настройки (пустой dict, если файла нет/битый)."""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = dict(data)
            for old, new in _KEY_MIGRATIONS.items():
                if new not in data and old in data:
                    data[new] = data[old]
            # Возвращаем только известные ключи — на случай старого/чужого файла.
            return {k: data[k] for k in PERSISTED_KEYS if k in data}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_settings(values: dict) -> None:
    """Записать настройки в settings.json (только PERSISTED_KEYS)."""
    data = {k: values[k] for k in PERSISTED_KEYS if k in values}
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def save_settings_if_changed(values: dict) -> bool:
    """Persist only when a sticky value changed; avoids fsync on every rerun."""
    data = {k: values[k] for k in PERSISTED_KEYS if k in values}
    if load_settings() == data:
        return False
    save_settings(data)
    return True
