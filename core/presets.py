"""Управление пресетами промптов: встроенные + пользовательские (presets.json)."""

from __future__ import annotations

import json
import os

from config import DEFAULT_SYSTEM_PROMPT, DEFAULT_USER_PROMPT, PRESETS_FILE

# Встроенные пресеты. Каждый: system + user prompt.
# Первый в словаре становится дефолтным при старте.
BUILTIN_PRESETS: dict[str, dict[str, str]] = {
    "Anima (structured, multi-char)": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "user": DEFAULT_USER_PROMPT,
    },
    "Detailed characters": {
        "system": (
            "You are an expert at creating detailed image captions for AI "
            "training datasets."
        ),
        "user": (
            "Analyze this image in detail. Describe: number of characters and "
            "their distinctive features (hair, clothing, body type, age, "
            "expression); their positions and relative placement in the frame; "
            "actions and interactions; art style, lighting, composition; "
            "clothing details and accessories.\n\n"
            "Format:\nFirst line: comma-separated tags\n"
            "Then: detailed natural English description in 2-4 sentences."
        ),
    },
    "Tag-heavy for SD": {
        "system": (
            "You are an expert at tagging images for Stable Diffusion training. "
            "You produce many precise booru-style tags plus a short description."
        ),
        "user": (
            "Tag this image for Stable Diffusion training.\n"
            "First line: a long comma-separated list of concise tags "
            "(subjects, count, hair, eyes, clothing, pose, expression, "
            "background, lighting, style, quality).\n"
            "Then: 1-2 sentences of natural description."
        ),
    },
    "Short description": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "user": (
            "Describe this image in one clear, natural English sentence, "
            "then add a few relevant comma-separated tags."
        ),
    },
    "Objective (neutral)": {
        "system": (
            "You are an objective image annotator for AI training datasets. "
            "Describe exactly what is visible in neutral, factual language, "
            "concisely and without embellishment."
        ),
        "user": (
            "Objectively describe everything visible in this image: "
            "subjects and their count, appearance, clothing, "
            "poses, positions, actions, setting and composition.\n"
            "First line: comma-separated tags.\n"
            "Then: 2-4 factual descriptive sentences."
        ),
    },
    "Maximum detail": {
        "system": (
            "You are a meticulous image analyst creating the most detailed possible "
            "captions for AI training datasets. Miss nothing."
        ),
        "user": (
            "Produce an exhaustive caption of this image.\n"
            "First line: an extensive comma-separated tag list.\n"
            "Then: a thorough multi-sentence description covering every character "
            "(count, age, hair, eyes, body, expression, clothing, accessories), "
            "their exact positions and interactions, all actions, the environment, "
            "lighting, color palette, camera angle, art style and mood."
        ),
    },
}


def load_presets() -> dict[str, dict[str, str]]:
    """Вернуть объединённый словарь пресетов: встроенные + сохранённые пользовательские."""
    presets = {name: dict(val) for name, val in BUILTIN_PRESETS.items()}
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                user_presets = json.load(f)
            if isinstance(user_presets, dict):
                for name, val in user_presets.items():
                    if isinstance(val, dict) and "system" in val and "user" in val:
                        presets[name] = {"system": val["system"], "user": val["user"]}
        except (OSError, json.JSONDecodeError):
            pass
    return presets


def _load_user_presets_raw() -> dict[str, dict[str, str]]:
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_preset(name: str, system_prompt: str, user_prompt: str) -> None:
    """Сохранить/обновить пользовательский пресет в presets.json."""
    if not name.strip():
        raise ValueError("Имя пресета не может быть пустым")
    data = _load_user_presets_raw()
    data[name] = {"system": system_prompt, "user": user_prompt}
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def delete_preset(name: str) -> bool:
    """Удалить пользовательский пресет. Встроенные удалить нельзя."""
    if name in BUILTIN_PRESETS:
        return False
    data = _load_user_presets_raw()
    if name in data:
        del data[name]
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    return False


def is_builtin(name: str) -> bool:
    return name in BUILTIN_PRESETS
