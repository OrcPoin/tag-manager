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


def sanitize_caption(
    caption: str,
    dedupe: bool = True,
    collapse_spaces: bool = True,
    lowercase: bool = False,
) -> str:
    """Нормализовать тег-строки капшена, не трогая прозу и скобочные блоки.

    Чистит то, что копится после генерации:
      * dedupe — убрать повторяющиеся теги (сравнение без учёта регистра,
        остаётся первое вхождение);
      * collapse_spaces — схлопнуть двойные пробелы ВНУТРИ тега ("blue  hair");
      * lowercase — привести теги к нижнему регистру.
    Пустые фрагменты и лишние запятые отсекаются самим _rebuild_tag_line.
    Идемпотентно: повторный прогон с теми же флагами ничего не меняет.
    """
    def _tf(frags):
        out = []
        seen = set()
        for f in frags:
            g = f.strip()
            if not g:
                continue
            if collapse_spaces:
                g = " ".join(g.split())
            if lowercase:
                g = g.lower()
            if dedupe:
                key = g.lower()
                if key in seen:
                    continue
                seen.add(key)
            out.append(g)
        return out

    return apply_to_tag_lines(caption, _tf)


# --------------------------------------------------------------------------- #
# Предупреждения по капшену (inline-подсветка в редакторе галереи)
# --------------------------------------------------------------------------- #
def caption_warnings(caption: str, trigger: str = "") -> list[str]:
    """Список предупреждений по капшену для UI."""
    from collections import Counter as _Counter

    from config import CAPTION_MAX_CHARS
    from core.quality import evaluate_caption

    warnings: list[str] = []
    text = (caption or "").strip()

    if not text:
        warnings.append("Капшен пустой")
        return warnings

    if len(text) > CAPTION_MAX_CHARS:
        warnings.append(f"Слишком длинный ({len(text)} > {CAPTION_MAX_CHARS} симв.)")

    if trigger and trigger.strip():
        first_line = text.split("\n", 1)[0]
        if trigger.strip().lower() not in first_line.lower():
            warnings.append(f"Нет триггера «{trigger.strip()}» в первой строке")

    tags = extract_tags(text)
    if tags:
        counts = _Counter(t.lower() for t in tags)
        dupes = [t for t, n in counts.items() if n > 1]
        if dupes:
            warnings.append(f"Дубли тегов: {', '.join(dupes[:5])}")

    is_good, reason = evaluate_caption(text)
    if not is_good:
        warnings.append(f"Качество: {reason}")

    return warnings


# --------------------------------------------------------------------------- #
# Мёрж старого и нового капшена (Фаза 5, «умное обновление»)
# --------------------------------------------------------------------------- #
# Наш формат строго секционный: сверху ТЕГ-секция (одна или несколько тег-строк,
# возможно разделённых пустыми строками), ниже — ПРОЗА (COMPOSITION/CHARACTERS/
# INTENT). Прозу нельзя слить автоматически (это семантика двух описаний),
# поэтому теги и прозу мёржим РАЗДЕЛЬНО, каждую по своей стратегии.
def split_sections(caption: str) -> tuple[str, str]:
    """Разбить капшен на (тег-секция, проза-секция).

    Проза начинается с ПЕРВОЙ прозаической строки (не тег и не пустая). Всё до
    неё — теги, от неё до конца — проза. Если прозы нет вовсе — вторая часть
    пустая; если капшен начинается сразу с прозы — пустая первая.
    """
    lines = caption.splitlines()
    prose_start = None
    for i, line in enumerate(lines):
        if not line.strip():
            continue  # пустые-разделители не начинают прозу
        if not is_tag_line(line):
            prose_start = i
            break
    if prose_start is None:
        return caption.strip("\n"), ""
    tag_part = "\n".join(lines[:prose_start]).strip("\n")
    prose_part = "\n".join(lines[prose_start:]).strip("\n")
    return tag_part, prose_part


def _union_missing_tags(old_tags: str, new_tags: str) -> str:
    """Добавить в тег-секцию old теги из new, которых там ещё нет (идемпотентно).

    Порядок и структура old сохраняются, недостающие теги дописываются в первую
    тег-строку через существующий add_tag_to_caption (та же логика, что в UI).
    Сравнение регистронезависимое, поэтому повторный прогон не плодит дубли.
    """
    if not old_tags.strip():
        return new_tags
    have = {t for t in extract_tags(old_tags)}
    result = old_tags
    for tag in extract_tags(new_tags):
        if tag not in have:
            result = add_tag_to_caption(result, tag)
            have.add(tag)
    return result


def merge_captions(
    old: str,
    new: str,
    tag_strategy: str = "union",
    prose_strategy: str = "keep_old",
) -> str:
    """Слить старый и новый капшен по раздельным стратегиям тегов и прозы.

    tag_strategy:
      "union"   — старые теги + недостающие из new (аддитивно, идемпотентно);
      "replace" — взять тег-секцию из new;
      "keep"    — оставить старые теги.
    prose_strategy:
      "keep_old" — сохранить старую прозу (защита ручных правок, unattended-дефолт);
      "replace"  — взять прозу из new.
    Возвращает собранный капшен «теги\\n\\nпроза». Пустые секции опускаются.
    """
    old_tags, old_prose = split_sections(old or "")
    new_tags, new_prose = split_sections(new or "")

    if tag_strategy == "replace":
        tags = new_tags
    elif tag_strategy == "keep":
        tags = old_tags
    else:  # union
        tags = _union_missing_tags(old_tags, new_tags)

    prose = new_prose if prose_strategy == "replace" else old_prose

    parts = [p for p in (tags.strip("\n"), prose.strip("\n")) if p.strip()]
    return "\n\n".join(parts)


def caption_fingerprint(caption: str) -> str:
    """Стабильная подпись содержимого капшена (для сравнения «изменилось ли»)."""
    from core.registry import caption_signature  # низкоуровневый модуль, без циклов
    return caption_signature(caption)


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

