"""Аудит готового датасета для вкладки «Здоровье» — чистая логика, без Streamlit.

Перед запуском обучения LoRA полезно за один проход найти то, что тихо портит
результат: битые файлы (роняют тренер), точные и почти-дубли (перекос выборки),
капшены-пустышки, не-RGB/анимированные картинки. Здесь — только вычисления;
Streamlit-обёртка и кэш живут в app.py.

Ключевые решения (см. согласованный план Фазы 3):
  * Без новых зависимостей: перцептивный хэш — это dhash на чистом PIL
    (grayscale → resize 9×8 → сравнение соседних пикселей → 64-битный int).
  * Один проход декодирования на файл: probe_and_hash разом отдаёт размеры,
    режим, формат, признак анимации, md5, dhash и признак «битый».
  * Починка = карантин (shutil.move в <folder>/_rejected/<причина>/ вместе с
    парным .txt), а НЕ удаление. Обратимо.
"""

from __future__ import annotations

import hashlib
import os
import shutil

from PIL import Image, ImageOps

from config import (
    CAPTION_MAX_CHARS,
    MIN_CAPTION_LENGTH,
    REJECTED_DIRNAME,
)
from core.dataset import read_caption, txt_path_for
from core.quality import evaluate_caption


# --------------------------------------------------------------------------- #
# Хэши
# --------------------------------------------------------------------------- #
def _dhash_from_image(im: Image.Image, hash_size: int = 8) -> int:
    """dhash из уже открытого изображения: горизонтальный градиент яркости.

    grayscale → resize до (hash_size+1)×hash_size → бит на каждую пару соседних
    пикселей (левый ярче правого). Итог — hash_size*hash_size-битное число.
    """
    small = im.convert("L").resize((hash_size + 1, hash_size), Image.BILINEAR)
    px = small.load()
    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            bits = (bits << 1) | (1 if px[col, row] > px[col + 1, row] else 0)
    return bits


def dhash(path: str, hash_size: int = 8) -> int | None:
    """Перцептивный хэш файла (None, если картинку не удалось открыть)."""
    try:
        with Image.open(path) as im:
            return _dhash_from_image(im, hash_size)
    except Exception:  # noqa: BLE001 — любой сбой декода = нет хэша
        return None


def file_md5(path: str, chunk: int = 1 << 20) -> str | None:
    """Потоковый md5 содержимого файла (для точных дублей). None при ошибке I/O."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(chunk), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def hamming(a: int, b: int) -> int:
    """Число различающихся бит двух хэшей."""
    return (a ^ b).bit_count()


# --------------------------------------------------------------------------- #
# Пер-файловый probe: размеры/режим/формат/анимация + md5 + dhash + «битость»
# --------------------------------------------------------------------------- #
def probe_and_hash(path: str, mtime: float, size: int) -> dict:
    """Единый проход по файлу: метаданные заголовка + оба хэша + детект битого.

    mtime/size в сигнатуре нужны Streamlit-кэшу (ключ = path+mtime+size): при
    повторном скане пересчитываются только изменившиеся файлы.

    Возврат: {ok, width, height, mode, format, animated, md5, dhash, error}.
    ok=False + error=... — файл не открылся/битый (декод упал).
    """
    result = {
        "ok": False, "width": 0, "height": 0, "mode": "", "format": "",
        "animated": False, "md5": None, "dhash": None, "error": "",
    }
    # md5 читает байты и не зависит от декодирования — считаем всегда.
    result["md5"] = file_md5(path)
    try:
        with Image.open(path) as im:
            result["width"], result["height"] = im.size
            result["mode"] = im.mode or ""
            result["format"] = im.format or ""
            result["animated"] = bool(getattr(im, "is_animated", False))
            # Ускоряем декод JPEG перед хэшем (для прочих форматов — no-op).
            try:
                im.draft("L", (16, 16))
            except Exception:  # noqa: BLE001
                pass
            result["dhash"] = _dhash_from_image(im)
            result["ok"] = True
    except Exception as exc:  # noqa: BLE001 — битый/усечённый/не картинка
        result["error"] = str(exc) or exc.__class__.__name__
    return result


# --------------------------------------------------------------------------- #
# Группировка дублей
# --------------------------------------------------------------------------- #
def group_exact(md5_by_path: dict[str, str]) -> list[list[str]]:
    """Группы файлов с одинаковыми байтами (точные дубли). Только группы ≥2."""
    buckets: dict[str, list[str]] = {}
    for path, digest in md5_by_path.items():
        if digest:
            buckets.setdefault(digest, []).append(path)
    return [sorted(paths) for paths in buckets.values() if len(paths) > 1]


def group_near(dhash_by_path: dict[str, int], threshold: int) -> list[list[str]]:
    """Группы визуально похожих картинок (Hamming(dhash) ≤ threshold).

    Union-find по всем парам. O(n²) popcount — для нескольких тысяч картинок
    приемлемо (замер в PLAN_STATUS). На десятках тысяч стоит перейти на BK-tree.
    Возвращаются только группы ≥2, внутри отсортировано по пути.
    """
    items = [(p, h) for p, h in dhash_by_path.items() if h is not None]
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        hi = items[i][1]
        for j in range(i + 1, n):
            if (hi ^ items[j][1]).bit_count() <= threshold:
                union(i, j)

    groups: dict[int, list[str]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(items[idx][0])
    return [sorted(paths) for paths in groups.values() if len(paths) > 1]


# --------------------------------------------------------------------------- #
# Здоровье капшенов и структурные коллизии
# --------------------------------------------------------------------------- #
def caption_issues(images: list[str], trigger: str = "") -> dict:
    """Классифицировать проблемы капшенов по списку картинок.

    Категории (списки путей картинок):
      empty         — .txt нет или пустой;
      short         — короче MIN_CAPTION_LENGTH;
      only_tags     — только теги без прозы (по evaluate_caption);
      too_long      — длиннее CAPTION_MAX_CHARS (риск обрезки);
      missing_trigger — не начинается с триггера (если триггер задан);
      unreadable    — .txt есть, но не читается как UTF-8.
    Категории не исключают друг друга (кроме empty, который замыкает на себя).
    """
    out = {k: [] for k in
           ("empty", "short", "only_tags", "too_long", "missing_trigger", "unreadable")}
    trig = trigger.strip().lower()
    for image in images:
        txt = txt_path_for(image)
        if os.path.exists(txt):
            try:
                with open(txt, "r", encoding="utf-8") as f:
                    caption = f.read()
            except (OSError, UnicodeDecodeError):
                out["unreadable"].append(image)
                continue
        else:
            caption = ""

        text = caption.strip()
        if not text:
            out["empty"].append(image)
            continue

        if len(text) < MIN_CAPTION_LENGTH:
            out["short"].append(image)
        if len(text) > CAPTION_MAX_CHARS:
            out["too_long"].append(image)

        is_good, reason = evaluate_caption(text)
        if not is_good and "только теги" in reason:
            out["only_tags"].append(image)

        if trig and not text.lower().startswith(trig):
            out["missing_trigger"].append(image)
    return out


def stem_collisions(images: list[str]) -> list[list[str]]:
    """Картинки с одинаковым именем, но разным расширением (a.jpg + a.png).

    Такие делят один a.txt — скрытый баг датасета: капшен относится лишь к одной
    из них, а тренер подхватит обе. Возвращаются группы путей (≥2), по общему stem.
    """
    by_stem: dict[str, list[str]] = {}
    for image in images:
        stem = os.path.splitext(image)[0]  # полный путь без расширения
        by_stem.setdefault(stem, []).append(image)
    return [sorted(paths) for paths in by_stem.values() if len(paths) > 1]


def format_issues(probes: dict[str, dict]) -> dict:
    """Из карты probe → категории формата/цвета.

      non_rgb  — режим не RGB (RGBA/L/P/CMYK и т.п.): при обучении цвет/альфа
                 могут поехать; лечится «Конвертировать в RGB».
      animated — многокадровые (gif/webp-анимация): тренер возьмёт первый кадр.
    Только по успешно открытым файлам (ok=True).
    """
    non_rgb, animated = [], []
    for path, p in probes.items():
        if not p.get("ok"):
            continue
        if p.get("mode") not in ("RGB", ""):
            non_rgb.append(path)
        if p.get("animated"):
            animated.append(path)
    return {"non_rgb": sorted(non_rgb), "animated": sorted(animated)}


def orphan_captions(folder: str, recursive: bool) -> list[str]:
    """.txt без парной картинки рядом (осиротевшие подписи).

    Зеркало dataset.find_caption_files (там — .txt С картинкой): тут возвращаем
    те, у кого поддерживаемого изображения рядом НЕТ. Служебные .txt (log/readme)
    тоже попадут — это ожидаемо, пользователь решает в карантин их или нет.
    """
    from config import SUPPORTED_EXTENSIONS

    if not folder or not os.path.isdir(folder):
        return []
    orphans: list[str] = []
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
            if not has_image:
                orphans.append(os.path.join(root, fn))
    return sorted(orphans)


# --------------------------------------------------------------------------- #
# Действия починки (карантин / конвертация) — обратимые
# --------------------------------------------------------------------------- #
def _unique_dest(dest_dir: str, filename: str) -> str:
    """Путь в dest_dir без затирания: при конфликте добавляет _1, _2, …"""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dest_dir, filename)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base}_{i}{ext}")
        i += 1
    return candidate


def quarantine(paths: list[str], dataset_folder: str, reason: str) -> int:
    """Перенести файлы (и их парные .txt) в <dataset_folder>/_rejected/<reason>/.

    Обратимо: ничего не удаляется, только move. Возврат — сколько файлов
    перенесено (без учёта .txt). При конфликте имён добавляется числовой суффикс.
    """
    dest_dir = os.path.join(dataset_folder, REJECTED_DIRNAME, reason)
    os.makedirs(dest_dir, exist_ok=True)
    moved = 0
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            shutil.move(path, _unique_dest(dest_dir, os.path.basename(path)))
            moved += 1
        except (OSError, shutil.Error):
            continue
        # Парный .txt тянем следом, если он есть и это не сам .txt-сирота.
        txt = txt_path_for(path)
        if txt != path and os.path.exists(txt):
            try:
                shutil.move(txt, _unique_dest(dest_dir, os.path.basename(txt)))
            except (OSError, shutil.Error):
                pass
    return moved


def convert_to_rgb(path: str, dataset_folder: str) -> bool:
    """Сконвертировать картинку в RGB на месте, сохранив оригинал в карантин.

    Оригинал уезжает в _rejected/nonrgb/ (обратимость), затем на исходный путь
    пишется RGB-версия с учётом EXIF-ориентации. True при успехе.
    """
    backup_dir = os.path.join(dataset_folder, REJECTED_DIRNAME, "nonrgb")
    os.makedirs(backup_dir, exist_ok=True)
    try:
        backup_path = _unique_dest(backup_dir, os.path.basename(path))
        shutil.copy2(path, backup_path)
        with Image.open(path) as im:
            rgb = ImageOps.exif_transpose(im).convert("RGB")
            ext = os.path.splitext(path)[1].lower()
            save_kwargs = {"quality": 95} if ext in (".jpg", ".jpeg") else {}
            rgb.save(path, **save_kwargs)
        return True
    except Exception:  # noqa: BLE001 — при сбое оставляем оригинал как есть
        return False
