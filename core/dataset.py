"""Работа с готовым датасетом капшенов: статистика тегов, массовые правки,
ретрофит триггер-слова. Чистая логика без Streamlit — тестируется отдельно.

Наш формат капшена — ГИБРИД (см. config.DEFAULT_USER_PROMPT):

    1girl, blue hair, smile, outdoors        <- тег-строка (comma-фрагменты)

    A medium shot, subject centered.         <- проза (COMPOSITION), точка

    (blue hair, on the left: she waves.)     <- проза персонажа, скобки

Поэтому «тег» определяется не по всему файлу, а только по ТЕГ-СТРОКАМ: строка
без прозы (не начинается со скобки, не содержит `.!?:`), внутри которой теги —
это comma-разделённые фрагменты из 1–3 слов (см. MAX_TAG_WORDS). Проза массовыми
операциями над тегами НЕ затрагивается — это защищает описания от порчи.
"""

from __future__ import annotations

import os
import shutil
from collections import Counter

from config import (
    BACKUP_SUFFIX,
    MAX_TAG_WORDS,
    SUPPORTED_EXTENSIONS,
)

# --------------------------------------------------------------------------- #
# Поиск файлов капшенов
# --------------------------------------------------------------------------- #
def find_caption_files(folder: str, recursive: bool) -> list[str]:
    """Пути ко всем .txt, у которых рядом лежит поддерживаемое изображение.

    Привязка к картинке отсекает служебные .txt (логи, readme), чтобы массовые
    операции не задели ничего лишнего.
    """
    if not folder or not os.path.isdir(folder):
        return []

    results: list[str] = []
    walker = os.walk(folder) if recursive else [(folder, [], os.listdir(folder))]
    for root, _dirs, files in walker:
        names = set(files)
        for fn in files:
            if not fn.lower().endswith(".txt"):
                continue
            stem = fn[:-4]
            has_image = any(
                (stem + ext) in names or (stem + ext.upper()) in names
                for ext in SUPPORTED_EXTENSIONS
            )
            if has_image:
                results.append(os.path.join(root, fn))
    return sorted(results)


# --------------------------------------------------------------------------- #
# Разбор тег-строк
# --------------------------------------------------------------------------- #
def is_tag_line(line: str) -> bool:
    """True, если строка — список тегов, а не проза.

    Проза в нашем формате: предложения COMPOSITION/INTENT (есть `.`/`!`/`?`) и
    скобочные блоки персонажей (начинаются с `(`, содержат `:`). Всё это должно
    остаться нетронутым при операциях над тегами.
    """
    s = line.strip()
    if not s:
        return False
    if s.startswith("("):
        return False
    if any(ch in s for ch in ".!?:"):
        return False
    return True


def _is_tag_fragment(fragment: str) -> bool:
    """Comma-фрагмент считается тегом, если это 1..MAX_TAG_WORDS слов."""
    f = fragment.strip()
    if not f:
        return False
    return 1 <= len(f.split()) <= MAX_TAG_WORDS


def extract_tags(caption: str) -> list[str]:
    """Все теги капшена в порядке появления (с повторами), нормализованные.

    Нормализация: strip + lowercase. Берём только фрагменты из тег-строк, чтобы
    короткие куски прозы ("smiling") не попадали в статистику.
    """
    tags: list[str] = []
    for line in caption.splitlines():
        if not is_tag_line(line):
            continue
        for frag in line.split(","):
            if _is_tag_fragment(frag):
                tags.append(frag.strip().lower())
    return tags


def tag_frequencies(files: list[str]) -> tuple[Counter, int]:
    """(Counter тег→сколько ФАЙЛОВ содержат его, число прочитанных файлов).

    Частота по файлам, а не по вхождениям: для датасета важно «в скольких
    примерах встречается тег», а не сколько раз суммарно.
    """
    counter: Counter = Counter()
    read = 0
    for path in files:
        text = _safe_read(path)
        if text is None:
            continue
        read += 1
        counter.update(set(extract_tags(text)))
    return counter, read


# --------------------------------------------------------------------------- #
# Массовые операции (на одном тексте — чистые, легко тестируются)
# --------------------------------------------------------------------------- #
def _rebuild_tag_line(line: str, transform) -> str:
    """Применить transform(list[str])->list[str] к тегам одной тег-строки.

    Не-тег-фрагменты (если вдруг затесались) сохраняются на своих местах —
    трансформируется только список, а склейка идёт через обычную ", ".
    """
    frags = [f.strip() for f in line.split(",")]
    new_frags = transform(frags)
    return ", ".join(f for f in new_frags if f)


def apply_to_tag_lines(caption: str, transform) -> str:
    """Применить transform к тегам КАЖДОЙ тег-строки, прозу оставить как есть."""
    out = []
    for line in caption.splitlines():
        if is_tag_line(line):
            out.append(_rebuild_tag_line(line, transform))
        else:
            out.append(line)
    return "\n".join(out)


def remove_tag_from_caption(caption: str, tag: str) -> str:
    """Удалить точный тег (регистронезависимо) из всех тег-строк."""
    target = tag.strip().lower()
    return apply_to_tag_lines(
        caption, lambda frags: [f for f in frags if f.strip().lower() != target]
    )


def add_tag_to_caption(caption: str, tag: str, at_start: bool = False) -> str:
    """Добавить тег в ПЕРВУЮ тег-строку, если его там ещё нет (идемпотентно)."""
    new = tag.strip()
    if not new:
        return caption
    lower = new.lower()
    lines = caption.splitlines()
    for i, line in enumerate(lines):
        if not is_tag_line(line):
            continue
        frags = [f.strip() for f in line.split(",") if f.strip()]
        if any(f.lower() == lower for f in frags):
            return caption  # уже есть
        frags = ([new] + frags) if at_start else (frags + [new])
        lines[i] = ", ".join(frags)
        return "\n".join(lines)
    # Тег-строк нет вовсе — добавляем первой строкой.
    return new + ("\n" + caption if caption else "")


def replace_whole_tag(caption: str, find: str, replace: str) -> str:
    """Заменить точный тег find→replace в тег-строках (по границам тега)."""
    src = find.strip().lower()
    dst = replace.strip()

    def _tf(frags):
        out = []
        for f in frags:
            out.append(dst if f.strip().lower() == src else f)
        return out

    return apply_to_tag_lines(caption, _tf)


def replace_substring(caption: str, find: str, replace: str) -> str:
    """Простая подстроковая замена по всему тексту (для опечаток и т.п.)."""
    if not find:
        return caption
    return caption.replace(find, replace)


# --------------------------------------------------------------------------- #
# Триггер-слово (ретрофит по всему датасету)
# --------------------------------------------------------------------------- #
def apply_trigger(caption: str, trigger: str) -> str:
    """Подставить триггер первым тегом (идемпотентно). Общая с core/worker."""
    caption = (caption or "").strip()
    trigger = (trigger or "").strip().strip(",").strip()
    if not trigger:
        return caption
    if caption.lower().startswith(trigger.lower()):
        return caption
    return f"{trigger}, {caption}"


def remove_trigger(caption: str, trigger: str) -> str:
    """Убрать триггер, если он стоит первым тегом."""
    caption = (caption or "").strip()
    trig = (trigger or "").strip().strip(",").strip()
    if not trig:
        return caption
    low = caption.lower()
    if low.startswith(trig.lower()):
        rest = caption[len(trig):].lstrip()
        if rest.startswith(","):
            rest = rest[1:].lstrip()
        return rest
    return caption


# --------------------------------------------------------------------------- #
# Ввод-вывод c предпросмотром и бэкапом
# --------------------------------------------------------------------------- #
def _safe_read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def preview_operation(files: list[str], op, limit: int = 8) -> dict:
    """Прогнать op(text)->text вхолостую. Вернуть сводку без записи на диск.

    {changed: int, total: int, unreadable: int, samples: [(name, before, after)]}
    samples — только для реально изменившихся файлов, до `limit` штук.
    """
    changed = 0
    unreadable = 0
    samples: list[tuple[str, str, str]] = []
    for path in files:
        text = _safe_read(path)
        if text is None:
            unreadable += 1
            continue
        new = op(text)
        if new != text:
            changed += 1
            if len(samples) < limit:
                samples.append((os.path.basename(path), text, new))
    return {
        "changed": changed,
        "total": len(files),
        "unreadable": unreadable,
        "samples": samples,
    }


def apply_operation(files: list[str], op, backup: bool = True) -> dict:
    """Применить op ко всем файлам. Пишет только изменившиеся.

    backup=True → перед перезаписью копия рядом с суффиксом BACKUP_SUFFIX
    (перезаписывает прошлый .bak, чтобы не плодить .bak.bak). Возврат:
    {changed, total, unreadable, errors}.
    """
    changed = 0
    unreadable = 0
    errors = 0
    for path in files:
        text = _safe_read(path)
        if text is None:
            unreadable += 1
            continue
        new = op(text)
        if new == text:
            continue
        try:
            if backup:
                shutil.copy2(path, path + BACKUP_SUFFIX)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
            changed += 1
        except OSError:
            errors += 1
    return {"changed": changed, "total": len(files), "unreadable": unreadable,
            "errors": errors}


def restore_backups(files: list[str]) -> int:
    """Откатить последнюю массовую операцию: .bak → .txt. Возврат: сколько откачено."""
    restored = 0
    for path in files:
        bak = path + BACKUP_SUFFIX
        if os.path.exists(bak):
            try:
                shutil.copy2(bak, path)
                restored += 1
            except OSError:
                pass
    return restored


def count_backups(files: list[str]) -> int:
    return sum(1 for p in files if os.path.exists(p + BACKUP_SUFFIX))


# --------------------------------------------------------------------------- #
# Галерея: просмотр/редактирование по одному изображению
# --------------------------------------------------------------------------- #
def txt_path_for(image_path: str) -> str:
    """Путь к .txt рядом с изображением (то же имя, расширение .txt)."""
    root, _ = os.path.splitext(image_path)
    return root + ".txt"


def read_caption(image_path: str) -> str:
    """Прочитать капшен изображения (пустая строка, если .txt нет/нечитаем)."""
    return _safe_read(txt_path_for(image_path)) or ""


def write_caption(image_path: str, caption: str, backup: bool = True) -> bool:
    """Записать капшен в .txt рядом с картинкой. True при успехе.

    backup=True и файл существует → сохранить прошлую версию в .bak (страховка
    от случайной затирки при ручном редактировании).
    """
    path = txt_path_for(image_path)
    try:
        if backup and os.path.exists(path):
            shutil.copy2(path, path + BACKUP_SUFFIX)
        with open(path, "w", encoding="utf-8") as f:
            f.write(caption.strip() + "\n")
        return True
    except OSError:
        return False


def list_gallery(
    folder: str,
    recursive: bool,
    only_missing: bool = False,
    search: str = "",
) -> list[dict]:
    """Список элементов галереи: по одному на изображение.

    Каждый элемент: {image, txt, caption, has_caption}. Фильтры:
      only_missing — оставить только картинки без (непустого) капшена;
      search       — регистронезависимая подстрока в тексте капшена
                     (напр. тег «blue hair» — покажет все фото с ним).
    """
    from core.image_scanner import find_images  # локальный импорт: без циклов

    needle = search.strip().lower()
    items: list[dict] = []
    for image in find_images(folder, recursive):
        caption = read_caption(image)
        has = bool(caption.strip())
        if only_missing and has:
            continue
        if needle and needle not in caption.lower():
            continue
        items.append({
            "image": image,
            "txt": txt_path_for(image),
            "caption": caption,
            "has_caption": has,
        })
    return items


def delete_captions(image_paths: list[str], backup: bool = True) -> int:
    """Удалить .txt-капшены выбранных изображений. Возврат: сколько удалено.

    backup=True → перед удалением сохранить копию в .bak, чтобы можно было
    восстановить через restore_backups.
    """
    deleted = 0
    for image in image_paths:
        path = txt_path_for(image)
        if not os.path.exists(path):
            continue
        try:
            if backup:
                shutil.copy2(path, path + BACKUP_SUFFIX)
            os.remove(path)
            deleted += 1
        except OSError:
            pass
    return deleted



# --------------------------------------------------------------------------- #
# Сводка по датасету (мини-«здоровье» для вкладки)
# --------------------------------------------------------------------------- #
def dataset_summary(folder: str, recursive: bool, trigger: str = "") -> dict:
    """Быстрые счётчики датасета для шапки вкладки «Теги».

    images            — всего изображений
    captions          — .txt-капшенов рядом с картинками
    images_no_caption — картинок без (непустого) капшена
    with_trigger      — капшенов, уже начинающихся с триггера (если задан)
    without_trigger   — капшенов без него (если триггер задан)
    backups           — сколько .bak лежит (есть что откатить)
    """
    from core.image_scanner import find_images  # локальный импорт: без циклов

    images = find_images(folder, recursive)
    cap_files = find_caption_files(folder, recursive)

    images_no_caption = 0
    for img in images:
        stem, _ = os.path.splitext(img)
        txt = stem + ".txt"
        text = _safe_read(txt)
        if text is None or not text.strip():
            images_no_caption += 1

    with_trigger = without_trigger = 0
    trig = (trigger or "").strip().strip(",").strip().lower()
    if trig:
        for path in cap_files:
            text = _safe_read(path) or ""
            if text.strip().lower().startswith(trig):
                with_trigger += 1
            else:
                without_trigger += 1

    return {
        "images": len(images),
        "captions": len(cap_files),
        "images_no_caption": images_no_caption,
        "with_trigger": with_trigger,
        "without_trigger": without_trigger,
        "backups": count_backups(cap_files),
    }

