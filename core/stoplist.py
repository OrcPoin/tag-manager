"""Стоп-лист тегов: автоматическое удаление нежелательных тегов при генерации."""

from __future__ import annotations

import os

from config import STOPLIST_FILE
from core.dataset import apply_to_tag_lines


def load_stoplist(path: str = STOPLIST_FILE) -> set[str]:
    """Загрузить стоп-лист из файла (один тег на строку, # = комментарий)."""
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return set()
    result: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        result.add(line.lower())
    return result


def save_stoplist(text: str, path: str = STOPLIST_FILE) -> None:
    """Записать текст стоп-листа в файл."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def apply_stoplist(caption: str, stoplist: set[str]) -> str:
    """Удалить все теги из стоп-листа из тег-строк капшена."""
    if not stoplist:
        return caption
    return apply_to_tag_lines(
        caption,
        lambda frags: [f for f in frags if f.strip().lower() not in stoplist],
    )
