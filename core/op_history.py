"""История массовых операций над датасетом с возможностью отката."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime

from core.dataset import restore_backups

HISTORY_FILE = ".tagmanager_history.json"
MAX_HISTORY = 30


@dataclass
class OpRecord:
    ts: str
    label: str
    files: list[str]


def _history_path(folder: str) -> str:
    return os.path.join(folder, HISTORY_FILE)


def log_operation(folder: str, label: str, files: list[str]) -> None:
    """Дописать запись в историю (последние MAX_HISTORY)."""
    if not files:
        return
    path = _history_path(folder)
    records: list[dict] = []
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                records = json.load(f)
        except (OSError, json.JSONDecodeError):
            records = []
    records.append(asdict(OpRecord(
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        label=label,
        files=files,
    )))
    records = records[-MAX_HISTORY:]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_history(folder: str) -> list[OpRecord]:
    """Загрузить историю операций."""
    path = _history_path(folder)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [OpRecord(**r) for r in data if isinstance(r, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def rollback_last(folder: str) -> tuple[int, str]:
    """Откатить последнюю операцию (.bak → .txt). Возврат: (кол-во откаченных, label)."""
    records = load_history(folder)
    if not records:
        return 0, ""
    last = records[-1]
    restored = restore_backups(last.files)
    records.pop()
    path = _history_path(folder)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in records], f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return restored, last.label
