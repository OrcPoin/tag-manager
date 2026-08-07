"""Чистые view-model функции для UI без зависимости от Streamlit."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceView:
    dataset_ready: bool
    phase: str
    title: str
    hint: str
    progress: float
    can_change_dataset: bool


def workspace_view(folder: str, scan_info: dict | None, snapshot: dict) -> WorkspaceView:
    ready = bool(folder and os.path.isdir(folder))
    total = int(snapshot.get("total", 0) or 0)
    done = int(snapshot.get("done", 0) or 0)
    progress = min(1.0, max(0.0, done / total)) if total else 0.0
    running = bool(snapshot.get("running"))
    if running and snapshot.get("has_review"):
        return WorkspaceView(ready, "review", "Проверьте текущий результат",
                             "Примите, исправьте, пропустите или перегенерируйте caption.",
                             progress, False)
    if running:
        return WorkspaceView(ready, "running", "Обработка выполняется",
                             "Следите за прогрессом; обработку можно приостановить или остановить.",
                             progress, False)
    if snapshot.get("finished") and snapshot.get("errors"):
        return WorkspaceView(ready, "errors", "Обработка завершена с ошибками",
                             "Откройте результаты и повторите обработку только проблемных файлов.",
                             progress, True)
    if snapshot.get("finished"):
        return WorkspaceView(ready, "finished", "Проверьте результат",
                             "Откройте результаты и проверьте готовые подписи.",
                             progress, True)
    if not ready:
        return WorkspaceView(False, "empty", "Выберите папку с изображениями",
                             "Выберите папку, которую нужно обработать.", 0.0, True)
    if not scan_info:
        return WorkspaceView(True, "unscanned", "Проверьте выбранную папку",
                             "До запуска посмотрите количество изображений и готовых подписей.", 0.0, True)
    return WorkspaceView(True, "ready", "Проверьте настройки",
                         "Можно проверить результат на одном изображении или сразу начать обработку.", 0.0, True)


def sync_dataset_context(session, folder: str, recursive: bool) -> None:
    """Синхронизировать общий dataset со старыми экранами миграционного UI."""
    session.folder = folder
    session.recursive = recursive
    session.gallery_folder = folder
    session.gallery_folder_input = folder
    session.gallery_recursive = recursive
    session.health_folder = folder
    session.health_recursive = recursive
