"""Вкладка «Теги»: массовые правки тегов готового датасета.

Под-вкладки: Триггер / Найти-заменить (+ чистка тегов + стоп-лист) / Тег /
Частоты / История. Пока идёт генерация — вкладка блокируется, чтобы массовая
правка не конфликтовала с записью .txt воркером.
"""

from __future__ import annotations

import os

import streamlit as st

from core import dataset as ds
from core import op_history
from ui.common import build_op, folder_picker_row
from ui.context import logger


def _stage(desc: tuple, label: str, files: list) -> None:
    """Посчитать предпросмотр операции и положить в ss.tags_pending (без записи)."""
    prev = ds.preview_operation(files, build_op(desc))
    st.session_state.tags_pending = {"desc": desc, "label": label, "preview": prev}


def render_tags_tab() -> None:
    ss = st.session_state
    ss.setdefault("tags_folder", "")
    ss.setdefault("tags_recursive", False)
    ss.setdefault("tags_files", [])
    ss.setdefault("tags_freq", None)
    ss.setdefault("tags_pending", None)
    ss.setdefault("tags_backup", True)

    st.subheader("Массовые правки тегов готового датасета")

    # Пока идёт генерация, воркер сам пишет .txt — параллельная массовая правка
    # затёрла бы результаты. Блокируем вкладку до остановки обработки.
    if ss.worker.is_alive():
        st.warning("Идёт генерация капшенов. Массовые правки заблокированы, чтобы "
                   "не конфликтовать с записью файлов — остановите обработку.")
        return

    folder, recursive = folder_picker_row(
        "tags_folder_input", "tags_rec", ss.tags_recursive,
        ss.tags_folder or ss.folder)

    if st.button("🔍 Сканировать датасет"):
        if os.path.isdir(folder):
            ss.tags_folder = folder
            ss.tags_recursive = recursive
            ss.tags_files = ds.find_caption_files(folder, recursive)
            ss.tags_freq = None
            ss.tags_pending = None
            st.toast(f"Капшенов найдено: {len(ss.tags_files)}")
        else:
            st.error("Папка не найдена")

    files = ss.tags_files
    if not files:
        st.info("Укажите папку датасета и нажмите «Сканировать датасет».")
        return

    # --- сводка по датасету ---
    summ = ds.dataset_summary(ss.tags_folder, ss.tags_recursive, ss.trigger_word)
    mc = st.columns(4)
    mc[0].metric("Капшенов", summ["captions"])
    mc[1].metric("Картинок без капшена", summ["images_no_caption"])
    if ss.trigger_word.strip():
        mc[2].metric("С триггером", summ["with_trigger"])
        mc[3].metric("Без триггера", summ["without_trigger"])
    else:
        mc[2].metric(".bak копий", summ["backups"])

    st.divider()
    op_tabs = st.tabs(["🎯 Триггер", "🔁 Найти/заменить", "➕➖ Тег",
                       "📊 Частоты", "📜 История"])

    with op_tabs[0]:
        trig = st.text_input("Триггер-слово", ss.trigger_word)
        tc = st.columns(2)
        if tc[0].button("Добавить во все", width="stretch",
                        disabled=not trig.strip()):
            _stage(("trigger_add", trig), f"Добавить триггер «{trig}»", files)
        if tc[1].button("Убрать из всех", width="stretch",
                        disabled=not trig.strip()):
            _stage(("trigger_del", trig), f"Убрать триггер «{trig}»", files)
        st.caption("Ретрофит триггера к уже готовым капшенам. Идемпотентно: "
                   "повторное добавление не дублирует, убирается только если стоит первым.")

    with op_tabs[1]:
        mode = st.radio(
            "Режим", ["Точный тег", "Подстрока"], horizontal=True,
            help="Точный тег — меняет тег целиком только в тег-строках, проза не "
                 "тронута. Подстрока — грубая замена по всему тексту (для опечаток).",
        )
        find = st.text_input("Найти")
        repl = st.text_input("Заменить на")
        if st.button("Предпросмотр замены", disabled=not find.strip()):
            if mode == "Точный тег":
                _stage(("replace_tag", find, repl), f"Тег «{find}» → «{repl}»", files)
            else:
                _stage(("replace_sub", find, repl),
                       f"Подстрока «{find}» → «{repl}»", files)

        st.divider()
        st.markdown("**Чистка тегов** — нормализация тег-строк (проза не тронута)")
        sc = st.columns(3)
        s_dedupe = sc[0].checkbox("Убрать дубли", True)
        s_ws = sc[1].checkbox("Схлопнуть пробелы", True)
        s_lower = sc[2].checkbox("В нижний регистр", False)
        if st.button("Предпросмотр чистки",
                     disabled=not (s_dedupe or s_ws or s_lower)):
            _stage(("sanitize", s_dedupe, s_ws, s_lower), "Чистка тегов", files)
        st.caption("Дубли — по совпадению без учёта регистра, остаётся первый. "
                   "«Схлопнуть пробелы» убирает двойные пробелы внутри тега. "
                   "Пустые фрагменты и лишние запятые убираются всегда.")

        st.divider()
        st.markdown("**Стоп-лист** — удалить нежелательные теги из всего датасета")
        from core.stoplist import load_stoplist as _load_sl2
        _sl = _load_sl2()
        if _sl:
            st.caption(f"Тегов в стоп-листе: {len(_sl)} (редактировать в сайдбаре)")
            if st.button("Применить стоп-лист к датасету"):
                _stage(("stoplist", frozenset(_sl)),
                       f"Стоп-лист ({len(_sl)} тегов)", files)
        else:
            st.caption("Стоп-лист пуст. Добавьте теги в сайдбаре → «Стоп-лист тегов».")

    with op_tabs[2]:
        ac = st.columns(2)
        with ac[0]:
            add_tag = st.text_input("Добавить тег")
            at_start = st.checkbox("В начало (первым тегом)")
            if st.button("Предпросмотр добавления", disabled=not add_tag.strip()):
                _stage(("add_tag", add_tag, at_start),
                       f"Добавить тег «{add_tag}»", files)
        with ac[1]:
            del_tag = st.text_input("Удалить тег")
            if st.button("Предпросмотр удаления", disabled=not del_tag.strip()):
                _stage(("del_tag", del_tag), f"Удалить тег «{del_tag}»", files)
        st.caption("Добавление идёт в первую тег-строку (идемпотентно). Удаление "
                   "убирает точный тег из всех тег-строк, прозу не трогает.")

    with op_tabs[3]:
        if st.button("Посчитать частоты"):
            ss.tags_freq = ds.tag_frequencies(files)
        if ss.tags_freq:
            counter, read = ss.tags_freq
            fc = st.columns([2, 1])
            asc = fc[0].checkbox("Редкие сверху (искать опечатки/мусор)")
            n = fc[1].number_input("Строк", 5, 1000, 40, 5)
            items = counter.most_common()
            if asc:
                items = items[::-1]
            rows = [{"тег": t, "файлов": c} for t, c in items[: int(n)]]
            st.caption(f"Уникальных тегов: {len(counter)} · прочитано файлов: {read}")
            st.dataframe(rows, width="stretch", height=380)

    with op_tabs[4]:
        hist = op_history.load_history(ss.tags_folder) if ss.tags_folder else []
        if not hist:
            st.caption("История операций пуста.")
        else:
            st.caption("Откат работает по `.bak`. Если после операции был ещё один "
                       "прогон — `.bak` перезаписан новым.")
            for rec in reversed(hist[-10:]):
                st.text(f"[{rec.ts}]  {rec.label}  ({len(rec.files)} файлов)")
            if st.button("↩️ Откатить последнюю операцию", width="stretch"):
                n_restored, lbl = op_history.rollback_last(ss.tags_folder)
                if n_restored:
                    st.toast(f"Откачено {n_restored} файлов: «{lbl}»")
                    ss.tags_freq = None
                    st.rerun()
                else:
                    st.warning("Нечего откатывать (нет .bak)")

    # --- общая staged-область: предпросмотр + применение ---
    pend = ss.tags_pending
    if pend:
        prev = pend["preview"]
        st.divider()
        st.markdown(f"### Предпросмотр — {pend['label']}")
        line = (f"Затронет **{prev['changed']}** из {prev['total']} файлов")
        if prev["unreadable"]:
            line += f" · нечитаемых: {prev['unreadable']}"
        st.write(line)
        for pi, (name, before, after) in enumerate(prev["samples"]):
            with st.expander(name):
                dc = st.columns(2)
                dc[0].text_area("до", before, height=150, disabled=True,
                                key=f"prev_before_{pi}")
                dc[1].text_area("после", after, height=150, disabled=True,
                                key=f"prev_after_{pi}")
        ss.tags_backup = st.checkbox("Сделать .bak перед записью (страховка)",
                                     ss.tags_backup)
        pc = st.columns(2)
        if pc[0].button("✅ Применить", type="primary",
                        disabled=prev["changed"] == 0, width="stretch"):
            res = ds.apply_operation(files, build_op(pend["desc"]),
                                     backup=ss.tags_backup)
            logger().info(f"Массовая правка «{pend['label']}»: изменено "
                          f"{res['changed']}/{res['total']}, ошибок {res['errors']}")
            if res["changed"] > 0 and ss.tags_folder:
                op_history.log_operation(
                    ss.tags_folder,
                    f"{pend['label']} → {res['changed']} файлов",
                    files,
                )
            st.toast(f"Изменено {res['changed']} файлов"
                     + (f", ошибок {res['errors']}" if res["errors"] else ""))
            ss.tags_pending = None
            ss.tags_freq = None
            st.rerun()
        if pc[1].button("Отмена", width="stretch"):
            ss.tags_pending = None
            st.rerun()

    # --- откат последней правки ---
    baks = ds.count_backups(files)
    if baks:
        st.divider()
        if st.button(f"↩️ Откатить последнюю правку (.bak → .txt, {baks} шт.)"):
            restored = ds.restore_backups(files)
            logger().info(f"Откат из .bak: {restored} файлов")
            st.toast(f"Откачено {restored} файлов")
            ss.tags_freq = None
            ss.tags_pending = None
            st.rerun()
