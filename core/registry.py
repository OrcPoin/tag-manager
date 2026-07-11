"""Реестр картинок, обработанных ИМЕННО этим приложением.

Позволяет докачивать датасет между запусками, не полагаясь на наличие .txt
(в датасете могут лежать чужие старые описания). Реестр — один скрытый файл
`.tagmanager_done.json` в корне папки датасета; ключи — пути картинок
относительно этой папки, значения — размер и дата картинки на момент обработки.
"""

from __future__ import annotations

import json
import os

REGISTRY_FILENAME = ".tagmanager_done.json"


def _registry_path(folder: str) -> str:
    return os.path.join(folder, REGISTRY_FILENAME)


class DoneRegistry:
    """Загрузка/проверка/пометка обработанных этим приложением картинок."""

    def __init__(self, folder: str):
        self.folder = folder
        self.path = _registry_path(folder)
        self.entries: dict[str, dict] = {}
        self._load()

    def _key(self, image_path: str) -> str:
        try:
            return os.path.relpath(image_path, self.folder).replace("\\", "/")
        except ValueError:
            # Другой диск (Windows) — используем абсолютный путь как запасной ключ.
            return os.path.abspath(image_path).replace("\\", "/")

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.entries = data.get("done", {}) if "done" in data else data
            except (OSError, json.JSONDecodeError):
                self.entries = {}

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"done": self.entries}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def is_done(self, image_path: str) -> bool:
        """True, если картинка обработана этим приложением и с тех пор не менялась."""
        entry = self.entries.get(self._key(image_path))
        if not entry:
            return False
        try:
            st = os.stat(image_path)
        except OSError:
            return False
        # Картинку могли заменить — тогда считаем её не обработанной.
        return (entry.get("size") == st.st_size
                and abs(entry.get("mtime", 0) - st.st_mtime) < 1.0)

    def mark_done(self, image_path: str, autosave: bool = True) -> None:
        try:
            st = os.stat(image_path)
        except OSError:
            return
        self.entries[self._key(image_path)] = {
            "size": st.st_size,
            "mtime": st.st_mtime,
        }
        if autosave:
            self.save()

    def count(self) -> int:
        return len(self.entries)
