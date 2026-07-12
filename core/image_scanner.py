"""Поиск изображений, сопоставление .txt файлов и фильтрация по режиму обработки."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from config import (
    MIN_TXT_SIZE_BYTES,
    MODE_ALL,
    MODE_ONLY_MISSING,
    MODE_RESUME,
    MODE_SKIP_PROCESSED,
    SUPPORTED_EXTENSIONS,
)
from core.quality import evaluate_caption
from core.registry import DoneRegistry


@dataclass
class ImageTask:
    """Одна единица работы: изображение и путь к его .txt файлу."""

    image_path: str
    txt_path: str
    status: str = "pending"  # pending | done | skipped | error
    caption: str = ""
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.image_path)


def _txt_path_for(image_path: str) -> str:
    root, _ = os.path.splitext(image_path)
    return root + ".txt"


def _has_valid_caption(txt_path: str) -> bool:
    """True, если .txt существует и его размер больше порога."""
    try:
        return os.path.getsize(txt_path) > MIN_TXT_SIZE_BYTES
    except OSError:
        return False


def _is_already_processed(image_path: str, txt_path: str) -> bool:
    """Режим 'пропускать обработанные': txt валиден и не старше изображения."""
    if not _has_valid_caption(txt_path):
        return False
    try:
        return os.path.getmtime(txt_path) >= os.path.getmtime(image_path)
    except OSError:
        return False


def find_images(folder: str, recursive: bool) -> list[str]:
    """Список путей к поддерживаемым изображениям в папке."""
    results: list[str] = []
    if not folder or not os.path.isdir(folder):
        return results

    if recursive:
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                if fn.lower().endswith(SUPPORTED_EXTENSIONS):
                    results.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(folder):
            full = os.path.join(folder, fn)
            if os.path.isfile(full) and fn.lower().endswith(SUPPORTED_EXTENSIONS):
                results.append(full)

    return sorted(results)


def build_task_list(
    folder: str,
    recursive: bool,
    mode: str,
    registry: DoneRegistry | None = None,
) -> list[ImageTask]:
    """Сформировать список задач согласно выбранному режиму обработки.

    Для режима MODE_RESUME используется реестр (`registry`): в список попадают
    только картинки, которые это приложение ещё НЕ обрабатывало (или картинка
    изменилась с тех пор). Наличие чужого старого .txt при этом игнорируется.
    """
    if mode == MODE_RESUME and registry is None:
        registry = DoneRegistry(folder)

    tasks: list[ImageTask] = []
    for image_path in find_images(folder, recursive):
        txt_path = _txt_path_for(image_path)

        if mode == MODE_RESUME:
            include = not registry.is_done(image_path)
        elif mode == MODE_ALL:
            include = True
        elif mode == MODE_ONLY_MISSING:
            include = not _has_valid_caption(txt_path)
        elif mode == MODE_SKIP_PROCESSED:
            include = not _is_already_processed(image_path, txt_path)
        else:
            include = True

        if include:
            tasks.append(ImageTask(image_path=image_path, txt_path=txt_path))

    return tasks


def scan_summary(folder: str, recursive: bool) -> dict:
    """Быстрая сводка: сколько всего изображений и сколько уже с капшенами."""
    images = find_images(folder, recursive)
    with_caption = sum(1 for p in images if _has_valid_caption(_txt_path_for(p)))
    return {
        "total": len(images),
        "with_caption": with_caption,
        "missing": len(images) - with_caption,
    }


# --------------------------------------------------------------------------- #
# Обновление существующих капшенов (Фаза 5)
# --------------------------------------------------------------------------- #
@dataclass
class UpdateTask:
    """Единица работы для режима обновления: картинка + её ТЕКУЩИЙ капшен."""

    image_path: str
    txt_path: str
    existing_caption: str = ""
    reason: str = "all"
    manually_edited: bool = False
    status: str = "pending"
    caption: str = ""
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.image_path)


def build_update_plan(
    folder: str,
    recursive: bool,
    registry: DoneRegistry,
    *,
    current_prompt_hash: str,
    current_model: str,
    filters: dict,
) -> list[UpdateTask]:
    """Сформировать план обновления: какие капшены нужно доработать и почему.

    filters — словарь bool-флагов:
      prompt_changed — промпт изменился с момента генерации;
      model_changed  — модель сменилась;
      quality        — текущий капшен не прошёл проверку качества;
      all            — включить все файлы с капшеном (полный перепрогон).
    Картинки БЕЗ .txt пропускаются (обновлять нечего — для них есть обычные режимы).
    """
    include_all = filters.get("all", False)
    check_prompt = filters.get("prompt_changed", False)
    check_model = filters.get("model_changed", False)
    check_quality = filters.get("quality", False)

    tasks: list[UpdateTask] = []
    for image_path in find_images(folder, recursive):
        txt_path = _txt_path_for(image_path)
        if not _has_valid_caption(txt_path):
            continue

        try:
            existing = open(txt_path, encoding="utf-8").read()
        except OSError:
            continue

        reason_parts: list[str] = []
        if include_all:
            reason_parts.append("all")
        else:
            if check_prompt and registry.prompt_changed(image_path, current_prompt_hash):
                reason_parts.append("prompt_changed")
            if check_model and registry.model_changed(image_path, current_model):
                reason_parts.append("model_changed")
            if check_quality:
                is_good, _ = evaluate_caption(existing)
                if not is_good:
                    reason_parts.append("quality")

        if not reason_parts:
            continue

        manually_edited = registry.was_edited_by_hand(image_path, existing)
        tasks.append(UpdateTask(
            image_path=image_path,
            txt_path=txt_path,
            existing_caption=existing,
            reason="+".join(reason_parts),
            manually_edited=manually_edited,
        ))

    return tasks
