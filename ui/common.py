"""Общие для нескольких вкладок UI-хелперы: выбор папки, миниатюры, форматирование.

Ничего app-специфичного — только переиспользуемые виджеты и кэш-функции.
"""

from __future__ import annotations

import os

import streamlit as st

import config
from core import health
from core import dataset as ds
from core.folder_dialog import pick_folder

# Параметры галереи/миниатюр (используются в gallery и health).
GALLERY_COLS = 6          # миниатюр в ряд
GALLERY_PAGE = 24         # миниатюр на страницу (кратно колонкам)
THUMB_PX = 200            # размер миниатюры (длинная сторона)


def browse_into(input_key: str) -> None:
    """Открыть системный диалог и записать выбранную папку в ss[input_key].

    Единая логика кнопки «📁 Обзор» для всех вкладок. ВАЖНО: вызывается как
    on_click-колбэк, а не инлайн. Колбэк выполняется ДО инстанцирования виджетов
    в следующем прогоне, поэтому запись в ключ виджета ввода легальна (инлайн-
    запись после создания text_input Streamlit запрещает). Пустой результат
    (диалог недоступен/отменён) — мягкий тост, поле не трогаем.
    """
    ss = st.session_state
    picked = pick_folder(ss.get(input_key, ""))
    if picked:
        ss[input_key] = picked
    else:
        st.toast("Диалог недоступен — введите путь вручную")


def folder_picker_row(input_key: str, rec_key: str, rec_default: bool,
                      default_folder: str) -> tuple[str, bool]:
    """Общий ряд выбора папки: текстовый путь + «📁 Обзор» + «Рекурсивно».

    Используется на вкладках «Теги» и «Здоровье» (у «Генерации»/«Галереи» свои
    компоновки). Ключи виджетов уникальны между вкладками. Путь из диалога пишем
    через on_click-колбэк (см. browse_into). vertical_alignment="bottom"
    выравнивает кнопку/галку по нижней кромке поля (иначе они уезжают вверх под
    подпись поля). Возврат: (путь, рекурсивно).
    """
    ss = st.session_state
    ss.setdefault(input_key, default_folder)
    c1, c2, c3 = st.columns([5, 1, 1], vertical_alignment="bottom")
    with c1:
        folder = st.text_input("Папка датасета", key=input_key)
    with c2:
        st.button("📁 Обзор", key=f"{input_key}_browse", width="stretch",
                  on_click=browse_into, args=(input_key,))
    with c3:
        recursive = st.checkbox("Рекурсивно", rec_default, key=rec_key)
    return folder, recursive


def build_op(desc: tuple):
    """Собрать колбэк-операцию над текстом капшена из примитивного дескриптора.

    Операция описывается кортежем (а не лямбдой в session_state) — так
    предпросмотр и применение переживают rerun и строят один и тот же колбэк из
    одних данных. Общий для вкладок «Теги» и «Галерея».
    """
    kind = desc[0]
    if kind == "trigger_add":
        return lambda t: ds.apply_trigger(t, desc[1])
    if kind == "trigger_del":
        return lambda t: ds.remove_trigger(t, desc[1])
    if kind == "replace_tag":
        return lambda t: ds.replace_whole_tag(t, desc[1], desc[2])
    if kind == "replace_sub":
        return lambda t: ds.replace_substring(t, desc[1], desc[2])
    if kind == "sanitize":
        return lambda t: ds.sanitize_caption(
            t, dedupe=desc[1], collapse_spaces=desc[2], lowercase=desc[3]
        )
    if kind == "add_tag":
        return lambda t: ds.add_tag_to_caption(t, desc[1], desc[2])
    if kind == "del_tag":
        return lambda t: ds.remove_tag_from_caption(t, desc[1])
    if kind == "stoplist":
        from core.stoplist import apply_stoplist as _apply_sl
        return lambda t: _apply_sl(t, desc[1])
    return lambda t: t


def fmt_duration(seconds: float) -> str:
    """Человекочитаемая длительность: '< 1 мин' / '~12 мин' / '~2 ч 5 мин'."""
    if seconds < 60:
        return "< 1 мин"
    m = int(seconds) // 60
    h = m // 60
    if h == 0:
        return f"~{m} мин"
    return f"~{h} ч {m % 60} мин"


@st.cache_data(show_spinner=False, max_entries=4096)
def thumbnail(path: str, mtime: float, size: int = THUMB_PX) -> bytes | None:
    """Уменьшенное превью (PNG-байты), с дисковым кэшем в .thumbs/.

    Порядок: RAM-кэш (@st.cache_data) → дисковый кэш (.thumbs/*.png) → PIL.
    Дисковый кэш переживает перезапуск приложения — повторный вход в галерею
    мгновенный даже на сотнях картинок.
    """
    import io

    from PIL import Image

    cache_dir = os.path.join(os.path.dirname(path), config.THUMBS_DIR)
    stem = os.path.splitext(os.path.basename(path))[0]
    cache_path = os.path.join(cache_dir, stem + ".png")

    try:
        if os.path.isfile(cache_path) and os.path.getmtime(cache_path) >= mtime:
            with open(cache_path, "rb") as f:
                return f.read()
    except OSError:
        pass

    try:
        im = Image.open(path)
        im.draft("RGB", (size, size))
        im = im.convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        data = buf.getvalue()
    except Exception:
        return None

    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(data)
    except OSError:
        pass

    return data


@st.cache_data(show_spinner=False, max_entries=8192)
def probe_cached(path: str, mtime: float, size: int) -> dict:
    """Кэш пер-файлового probe+hash по (path, mtime, size).

    Повторный скан датасета пересчитывает только изменившиеся файлы — на тысячах
    картинок это разница между «секунды» и «десятки секунд». mtime/size в ключе
    гарантируют инвалидацию при подмене файла.
    """
    return health.probe_and_hash(path, mtime, size)
