"""Экспорт dataset-конфига для тренеров (OneTrainer, kohya)."""

from __future__ import annotations

import json
import os


def _toml_escape(s: str) -> str:
    """Экранировать строку для вставки в двойные кавычки TOML."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def export_onetrainer(
    folder: str,
    trigger: str = "",
    repeats: int = 10,
    resolution: int = 512,
) -> str:
    """JSON-конфиг OneTrainer dataset concept."""
    concept = {
        "name": os.path.basename(folder) or "dataset",
        "path": os.path.abspath(folder),
        "text": {
            "condition": "path",
            "default_caption": "",
        },
        "image": {
            "enable_crop_jitter": True,
            "resolution": resolution,
        },
        "repeats": repeats,
        "balancing": 1.0,
    }
    if trigger.strip():
        concept["text"]["activation_text"] = trigger.strip()
        concept["text"]["activation_text_position"] = "prepend"
    return json.dumps({"concepts": [concept]}, indent=2, ensure_ascii=False)


def export_kohya_toml(
    folder: str,
    trigger: str = "",
    repeats: int = 10,
    resolution: int = 512,
) -> str:
    """TOML-конфиг kohya dataset subset (без зависимости от toml-библиотек)."""
    abs_path = _toml_escape(os.path.abspath(folder).replace("\\", "/"))
    lines = [
        "[general]",
        f"resolution = {resolution}",
        "shuffle_caption = true",
        "keep_tokens = 1",
        "",
        "[[datasets]]",
        "",
        "[[datasets.subsets]]",
        f'image_dir = "{abs_path}"',
        'caption_extension = ".txt"',
        f"num_repeats = {repeats}",
    ]
    if trigger.strip():
        lines.append(f'activation_text = "{_toml_escape(trigger.strip())}"')
    return "\n".join(lines) + "\n"
