"""Вкладка «Галерея»: просмотр/правка капшенов по одному фото + мультидействия.

Скан читает список изображений и капшены ОДИН раз в session_state; фильтрация и
поиск дальше идут по памяти — это держит UI отзывчивым на больших датасетах.
"""

from __future__ import annotations

import os

import streamlit as st

from core import dataset as ds
from core import op_history
from core.image_scanner import ImageTask
from core.registry import DoneRegistry
from ui.common import (
    GALLERY_COLS,
    GALLERY_PAGE,
    browse_into,
    build_op,
    thumbnail,
)
from ui.context import get_client, get_params, logger


def _gallery_registry() -> DoneRegistry:
    """Реестр «сделано этим приложением» для ПАПКИ ГАЛЕРЕИ.

    Важно строить его именно по ss.gallery_folder, а не через context.get_registry()
    (тот привязан к ss.folder вкладки «Генерация»). Иначе перегенерация из галереи
    записала бы провенанс в .tagmanager_done.json папки генерации, а не туда, где
    реально лежат перегенерированные картинки — и «Докачать»/«Обновить» по этой
    папке потом считали бы файлы несделанными.
    """
    return DoneRegistry(st.session_state.gallery_folder)


def _filtered() -> list[dict]:
    """Применить фильтры «без капшена»/поиск к ss.gallery_all (in-memory, быстро)."""
    ss = st.session_state
    items = ss.gallery_all
    if ss.gallery_only_missing:
        items = [it for it in items if not it["has_caption"]]
    needle = ss.gallery_search.strip().lower()
    if needle:
        items = [it for it in items if needle in it["caption"].lower()]
    return items


def _scan(folder: str, recursive: bool) -> None:
    """Читает список изображений и капшены ОДИН раз, кладёт в session_state.

    Дальше фильтрация/поиск идут по памяти, диск на ререндерах не трогается —
    это и держит UI отзывчивым на больших датасетах.
    """
    ss = st.session_state
    ss.gallery_folder = folder
    ss.gallery_recursive = recursive
    ss.gallery_all = ds.list_gallery(folder, recursive)
    ss.gallery_page = 0
    ss.gallery_open = None
    ss.gallery_selected = set()
    ss.gallery_pending = None


def _editor(items: list[dict]) -> None:
    """Полноэкранный редактор одного фото с навигацией ‹ ›."""
    ss = st.session_state
    # Находим позицию открытого фото в ТЕКУЩЕМ отфильтрованном списке.
    paths = [it["image"] for it in items]
    if ss.gallery_open not in paths:
        # Фото выпало из фильтра (напр. капшен добавлен) — выходим в сетку.
        ss.gallery_open = None
        st.rerun()
    idx = paths.index(ss.gallery_open)
    item = items[idx]

    top = st.columns([1, 1, 4, 1])
    if top[0].button("⬅️ К сетке", width="stretch"):
        ss.gallery_open = None
        st.rerun()
    if top[1].button("‹ Пред", width="stretch", disabled=idx == 0):
        ss.gallery_open = paths[idx - 1]
        st.rerun()
    top[2].markdown(
        f"**{os.path.basename(item['image'])}** &nbsp; · &nbsp; {idx + 1} из {len(items)}"
    )
    if top[3].button("След ›", width="stretch", disabled=idx >= len(items) - 1):
        ss.gallery_open = paths[idx + 1]
        st.rerun()

    img_col, edit_col = st.columns([1, 1])
    with img_col:
        if os.path.exists(item["image"]):
            st.image(item["image"], width="stretch")
    with edit_col:
        # Ключ привязан к пути + nonce — при переходе на другое фото ИЛИ после
        # перегенерации (nonce++) text_area пересоздаётся со свежим капшеном
        # (а не держит старый текст своего ключа).
        key = f"gal_edit_{ss.gallery_edit_nonce}_{item['image']}"
        edited = st.text_area("Капшен", item["caption"], height=320, key=key)

        busy = ss.worker.is_alive()
        bcols = st.columns(2)
        if bcols[0].button("💾 Сохранить", type="primary", width="stretch",
                           disabled=busy):
            if ds.write_caption(item["image"], edited, backup=True):
                item["caption"] = edited.strip()
                item["has_caption"] = bool(item["caption"])
                st.toast("Капшен сохранён")
                st.rerun()
            else:
                st.error("Не удалось записать файл")
        if bcols[1].button("🔄 Перегенерировать", width="stretch",
                           disabled=busy,
                           help="Сгенерировать капшен этого фото заново через LLM. "
                                "Займёт столько же, сколько обычная генерация одного файла."):
            task = ImageTask(image_path=item["image"], txt_path=item["txt"])
            # manual_review=False → воркер запишет результат сразу, без паузы.
            params = {**get_params(), "manual_review": False}
            ss.worker.start([task], ss.gallery_folder, params, logger(),
                            _gallery_registry(), get_client())
            ss.gallery_regen = {item["image"]}  # подхватим новый .txt по завершении
            st.toast("Перегенерация запущена…")
            st.rerun()

        if busy:
            st.caption("⏳ Идёт генерация — сохранение и перегенерация временно "
                       "заблокированы.")

        # Предупреждения по капшену
        _warns = ds.caption_warnings(edited, ss.trigger_word)
        for _w in _warns:
            st.warning(_w)

        # Показ тегов чипами для наглядности.
        tags = ds.extract_tags(item["caption"])
        if tags:
            st.caption("Теги: " + "  ".join(f"`{t}`" for t in tags[:40]))


def _multiaction(items: list[dict]) -> None:
    """Панель действий над выбранными фото (мультивыбор)."""
    ss = st.session_state
    sel = [it for it in items if it["image"] in ss.gallery_selected]
    st.markdown(f"**Выбрано: {len(sel)}**")
    if not sel:
        st.caption("Отметьте фото галочками, чтобы применить действие к нескольким сразу.")
        return

    sel_txt = [it["txt"] for it in sel]
    sel_imgs = [it["image"] for it in sel]
    ac = st.columns([2, 2, 1])
    tag = ac[0].text_input("Тег для добавления/удаления", key="gal_ma_tag")
    trig = ac[1].text_input("Триггер", ss.trigger_word, key="gal_ma_trig")

    b = st.columns(5)
    if b[0].button("➕ Добавить тег", disabled=not tag.strip(), width="stretch"):
        ss.gallery_pending = (("add_tag", tag, False), f"Добавить тег «{tag}»", sel_txt)
    if b[1].button("➖ Удалить тег", disabled=not tag.strip(), width="stretch"):
        ss.gallery_pending = (("del_tag", tag), f"Удалить тег «{tag}»", sel_txt)
    if b[2].button("🎯 +Триггер", disabled=not trig.strip(), width="stretch"):
        ss.gallery_pending = (("trigger_add", trig), f"Добавить триггер «{trig}»", sel_txt)
    if b[3].button("🗑️ Удалить капшены", width="stretch"):
        ss.gallery_pending = (("delete", sel_imgs), "Удалить капшены выбранных", sel_txt)
    _busy = ss.worker.is_alive()
    if b[4].button("🔄 Перегенерировать", width="stretch", disabled=_busy):
        tasks = [ImageTask(image_path=it["image"], txt_path=it["txt"]) for it in sel]
        params = {**get_params(), "manual_review": False}
        ss.worker.start(tasks, ss.gallery_folder, params, logger(),
                        _gallery_registry(), get_client())
        ss.gallery_regen = {it["image"] for it in sel}  # обновим по завершении
        st.toast(f"Перегенерация {len(tasks)} файлов запущена…")
        ss.gallery_pending = None
        ss.gallery_selected = set()
        st.rerun()

    pend = ss.gallery_pending
    if pend:
        desc, label, target = pend
        st.divider()
        st.markdown(f"### Предпросмотр — {label}")
        if desc[0] == "delete":
            st.write(f"Будет удалено **{len(desc[1])}** .txt (с .bak-копией).")
        else:
            prev = ds.preview_operation(target, build_op(desc))
            st.write(f"Затронет **{prev['changed']}** из {prev['total']} файлов.")
            for gi, (name, before, after) in enumerate(prev["samples"][:4]):
                with st.expander(name):
                    dc = st.columns(2)
                    dc[0].text_area("до", before, height=120, disabled=True,
                                    key=f"gma_b_{gi}")
                    dc[1].text_area("после", after, height=120, disabled=True,
                                    key=f"gma_a_{gi}")
        pc = st.columns(2)
        if pc[0].button("✅ Применить", type="primary", width="stretch"):
            if desc[0] == "delete":
                n = ds.delete_captions(desc[1], backup=True)
                msg = f"Удалено капшенов: {n}"
            else:
                res = ds.apply_operation(target, build_op(desc), backup=True)
                msg = f"Изменено {res['changed']} файлов"
            logger().info(f"Галерея, мультидействие «{label}»: {msg}")
            if ss.gallery_folder:
                op_history.log_operation(ss.gallery_folder, f"{label}: {msg}", target)
            st.toast(msg)
            # Обновляем капшены выбранных в памяти, чтобы сетка показала актуальное.
            for it in sel:
                it["caption"] = ds.read_caption(it["image"])
                it["has_caption"] = bool(it["caption"].strip())
            ss.gallery_pending = None
            st.rerun()
        if pc[1].button("Отмена", width="stretch"):
            ss.gallery_pending = None
            st.rerun()


def _grid(items: list[dict]) -> None:
    """Сетка миниатюр с пагинацией, чекбоксами выбора и кнопкой «открыть»."""
    ss = st.session_state
    total = len(items)
    pages = max(1, (total + GALLERY_PAGE - 1) // GALLERY_PAGE)
    ss.gallery_page = min(ss.gallery_page, pages - 1)

    nav = st.columns([1, 2, 1, 2])
    if nav[0].button("‹", width="stretch", disabled=ss.gallery_page == 0):
        ss.gallery_page -= 1
        st.rerun()
    nav[1].markdown(f"<div style='text-align:center'>Стр. {ss.gallery_page + 1} / {pages} "
                    f"· фото: {total}</div>", unsafe_allow_html=True)
    if nav[2].button("›", width="stretch", disabled=ss.gallery_page >= pages - 1):
        ss.gallery_page += 1
        st.rerun()
    with nav[3]:
        sc = st.columns(2)
        if sc[0].button("Выбрать стр.", width="stretch"):
            for it in items[ss.gallery_page * GALLERY_PAGE:(ss.gallery_page + 1) * GALLERY_PAGE]:
                ss.gallery_selected.add(it["image"])
            st.rerun()
        if sc[1].button("Снять выбор", width="stretch"):
            ss.gallery_selected = set()
            st.rerun()

    start = ss.gallery_page * GALLERY_PAGE
    page_items = items[start:start + GALLERY_PAGE]

    # Рисуем ТОЛЬКО текущую страницу → максимум GALLERY_PAGE миниатюр за ререндер.
    for row_start in range(0, len(page_items), GALLERY_COLS):
        row = page_items[row_start:row_start + GALLERY_COLS]
        cols = st.columns(GALLERY_COLS)
        for col, it in zip(cols, row):
            with col:
                try:
                    mt = os.path.getmtime(it["image"])
                except OSError:
                    mt = 0.0
                thumb = thumbnail(it["image"], mt)
                if thumb is not None:
                    st.image(thumb, width="stretch")
                else:
                    st.caption("🖼️ (нет превью)")
                mark = "✅" if it["has_caption"] else "⚠️"
                checked = it["image"] in ss.gallery_selected
                new_checked = st.checkbox(
                    f"{mark} {os.path.basename(it['image'])[:16]}",
                    value=checked, key=f"gal_sel_{it['image']}",
                )
                if new_checked and not checked:
                    ss.gallery_selected.add(it["image"])
                elif not new_checked and checked:
                    ss.gallery_selected.discard(it["image"])
                if st.button("✏️ Открыть", key=f"gal_open_{it['image']}",
                             width="stretch"):
                    ss.gallery_open = it["image"]
                    ss.gallery_pending = None
                    st.rerun()


def render_gallery_tab() -> None:
    ss = st.session_state
    ss.setdefault("gallery_folder", "")
    ss.setdefault("gallery_recursive", False)
    ss.setdefault("gallery_all", [])
    ss.setdefault("gallery_page", 0)
    ss.setdefault("gallery_open", None)
    ss.setdefault("gallery_selected", set())
    ss.setdefault("gallery_only_missing", False)
    ss.setdefault("gallery_search", "")
    ss.setdefault("gallery_pending", None)
    ss.setdefault("gallery_regen", set())   # пути, отданные на перегенерацию
    ss.setdefault("gallery_edit_nonce", 0)  # сброс text_area редактора после refresh

    # Если галерея запускала перегенерацию и воркер закончил — капшены в памяти
    # (ss.gallery_all) устарели: воркер записал новый .txt на диск, а список мы
    # читали один раз при скане. Подтягиваем свежие капшены только для затронутых
    # файлов и «пересобираем» text_area редактора через nonce (иначе виджет
    # держит старый текст по своему ключу).
    if ss.gallery_regen and not ss.worker.is_alive():
        by_path = {it["image"]: it for it in ss.gallery_all}
        refreshed = 0
        for img in ss.gallery_regen:
            it = by_path.get(img)
            if it is None:
                continue
            it["caption"] = ds.read_caption(img)
            it["has_caption"] = bool(it["caption"].strip())
            refreshed += 1
        ss.gallery_regen = set()
        ss.gallery_edit_nonce += 1
        if refreshed:
            st.toast(f"Капшены обновлены: {refreshed}")

    st.subheader("Галерея — просмотр и правка капшенов")

    ss.setdefault("gallery_folder_input", ss.gallery_folder or ss.folder)
    c = st.columns([5, 1, 1, 1], vertical_alignment="bottom")
    folder = c[0].text_input("Папка датасета", key="gallery_folder_input")
    c[1].button("📁 Обзор", key="gallery_browse", width="stretch",
                on_click=browse_into, args=("gallery_folder_input",))
    recursive = c[2].checkbox("Рекурсивно", ss.gallery_recursive,
                              key="gallery_recursive_cb")
    if c[3].button("🔍 Сканировать", width="stretch"):
        if os.path.isdir(folder):
            _scan(folder, recursive)
            st.toast(f"Изображений: {len(ss.gallery_all)}")
            st.rerun()
        else:
            st.error("Папка не найдена")

    if not ss.gallery_all:
        st.info("Укажите папку и нажмите «Сканировать».")
        return

    # Фильтры (in-memory, быстрые даже на тысячах файлов).
    fc = st.columns([1, 3])
    ss.gallery_only_missing = fc[0].checkbox("Только без капшена",
                                             ss.gallery_only_missing)
    ss.gallery_search = fc[1].text_input("Поиск по тексту капшена (тег/слово)",
                                         ss.gallery_search)

    items = _filtered()
    if not items:
        st.warning("Под фильтр ничего не подходит.")
        return

    # Открытое фото → редактор; иначе сетка + панель мультидействий.
    if ss.gallery_open:
        _editor(items)
    else:
        _grid(items)
        st.divider()
        with st.expander(f"⚙️ Действия над выбранными ({len(ss.gallery_selected)})",
                         expanded=bool(ss.gallery_selected)):
            _multiaction(items)
