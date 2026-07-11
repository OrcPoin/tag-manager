"""Состояние обработки: очередь, статистика, флаги и сохранение прогресса на диск."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from config import PROGRESS_FILE
from core.image_scanner import ImageTask


class ProcessingState:
    """Хранит очередь задач, текущий индекс, статистику и управляющие флаги."""

    def __init__(self):
        self.tasks: list[ImageTask] = []
        self.index: int = 0
        self.running: bool = False
        self.paused: bool = False
        self.folder: str = ""
        # статистика
        self.processed: int = 0
        self.skipped: int = 0
        self.errors: int = 0

    # --- Очередь ---
    def set_tasks(self, tasks: list[ImageTask], folder: str) -> None:
        self.tasks = tasks
        self.index = 0
        self.folder = folder
        self.processed = 0
        self.skipped = 0
        self.errors = 0

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def done_count(self) -> int:
        return self.index

    def current_task(self) -> ImageTask | None:
        if 0 <= self.index < len(self.tasks):
            return self.tasks[self.index]
        return None

    def is_finished(self) -> bool:
        return self.index >= len(self.tasks)

    def advance(self) -> None:
        self.index += 1

    # --- Сохранение / восстановление прогресса ---
    def save_progress(self) -> None:
        data = {
            "folder": self.folder,
            "index": self.index,
            "processed": self.processed,
            "skipped": self.skipped,
            "errors": self.errors,
            "tasks": [asdict(t) for t in self.tasks],
        }
        try:
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def load_progress(self) -> bool:
        """Восстановить прогресс из файла. True при успехе."""
        if not os.path.exists(PROGRESS_FILE):
            return False
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.folder = data.get("folder", "")
            self.index = data.get("index", 0)
            self.processed = data.get("processed", 0)
            self.skipped = data.get("skipped", 0)
            self.errors = data.get("errors", 0)
            self.tasks = [
                ImageTask(**{k: t.get(k, "") for k in
                             ("image_path", "txt_path", "status", "caption", "error")})
                for t in data.get("tasks", [])
            ]
            return True
        except (OSError, json.JSONDecodeError, TypeError):
            return False

    def clear_progress(self) -> None:
        try:
            if os.path.exists(PROGRESS_FILE):
                os.remove(PROGRESS_FILE)
        except OSError:
            pass

    def stats(self) -> dict:
        return {
            "total": self.total,
            "processed": self.processed,
            "skipped": self.skipped,
            "errors": self.errors,
            "done": self.done_count,
        }
