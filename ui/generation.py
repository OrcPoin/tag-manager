"""Вкладка «Генерация»: выбор папки/режима, пресеты/промпты, запуск обработки,
ручное ревью и лог.

Секции: 1) Папка и режим (+ настройки обновления для MODE_UPDATE),
2) Пресет и промпты, 3) Обработка (start/pause/stop + прогресс/ETA),
4) Лог. Возвращает флаг `poll` — нужно ли главному циклу перерисоваться.
"""

from __future__ import annotations

import os
import time

import streamlit as st

import config
from core import presets as presets_mod
from core.folder_dialog import pick_folder
from core.image_scanner import (
    build_task_list,
    build_update_plan,
    find_images,
    scan_summary,
)
from core.registry import prompt_signature
from ui.common import fmt_duration
from ui.context import get_client, get_params, get_registry, logger


def render_review(task, preview_col, caption_col) -> None:
    ss = st.session_state
    worker = ss.worker
    with preview_col:
        st.markdown(f"**Текущий файл:** `{task.name}`")
        if os.path.exists(task.image_path):
            st.image(task.image_path, width="stretch")
    with caption_col:
        st.markdown("**Сгенерированный капшен:**")
        # Streamlit-виджет с фиксированным key держит своё значение в session_state
        # и ИГНОРИРУЕТ аргумент value при перерисовке — поэтому при переходе на новый
        # файл (или после «Перегенерировать») поле показывало бы старый текст. Ручная
        # синхронизация через session_state оказалась хрупкой: между файлами панель
        # ревью ~10 мин не рисуется (has_review=False, пока генерится следующий), и
        # Streamlit подчищает ключ review_caption как «исчезнувший виджет», рассинхронив
        # его с guard-ключом. Поэтому — как в галерее (nonce в ключе): при смене
        # (файл, капшен) наращиваем nonce, ключ становится новым → рождается свежий
        # виджет, чей value=task.caption честно применяется. Пока пользователь правит
        # текст, (файл, капшен) не меняется → ключ стабилен → правки не затираются.
        ident = (task.name, task.caption)
        if ss.get("_review_ident") != ident:
            ss["_review_ident"] = ident
            ss["_review_nonce"] = ss.get("_review_nonce", 0) + 1
        edited = st.text_area(
            "Капшен", task.caption, height=220,
            key=f"review_caption_{ss.get('_review_nonce', 0)}",
        )
        b1, b2, b3, b4 = st.columns(4)
        if b1.button("✅ Принять"):
            worker.submit_review("accept", edited)
            time.sleep(0.3)
            st.rerun()
        if b2.button("🔄 Перегенерировать"):
            worker.submit_review("regenerate")
            time.sleep(0.3)
            st.rerun()
        if b3.button("✏️ Сохранить правку"):
            worker.submit_review("edit", edited)
            time.sleep(0.3)
            st.rerun()
        if b4.button("⏭️ Пропустить"):
            worker.submit_review("skip")
            time.sleep(0.3)
            st.rerun()


def render_generation_tab() -> bool:
    """Отрисовать вкладку «Генерация». Возврат: нужно ли поллинг-rerun."""
    ss = st.session_state
    worker = ss.worker

    # --- Выбор папки и режима ---
    st.subheader("1. Папка и режим")
    col_f1, col_f2 = st.columns([5, 1], vertical_alignment="bottom")
    with col_f1:
        ss.folder = st.text_input("Путь к папке с изображениями", ss.folder)
    with col_f2:
        if st.button("📁 Обзор", width="stretch"):
            picked = pick_folder(ss.folder)
            if picked:
                ss.folder = picked
                st.rerun()
            else:
                st.toast("Диалог недоступен — введите путь вручную")

    col_m1, col_m2 = st.columns([2, 3])
    with col_m1:
        ss.recursive = st.checkbox("Рекурсивно (включая подпапки)", ss.recursive)
    with col_m2:
        st.selectbox("Режим обработки", config.UI_MODES, key="mode")

    # --- Настройки обновления (только для MODE_UPDATE) ---
    if ss.mode == config.MODE_UPDATE:
        with st.expander("⚙️ Настройки обновления", expanded=True):
            uc1, uc2 = st.columns(2)
            with uc1:
                ss.update_mechanism = st.selectbox(
                    "Механизм", config.UPDATE_MECHANISMS,
                    index=config.UPDATE_MECHANISMS.index(
                        ss.get("update_mechanism", config.DEFAULT_UPDATE_MECHANISM)),
                    help="«Дополнить» — модель видит старый капшен и дописывает "
                         "недостающее (дешевле). «Полная регенерация» — генерит "
                         "с нуля, потом мёржит по стратегии.",
                )
                ss.tag_strategy = st.selectbox(
                    "Теги", config.TAG_STRATEGIES,
                    index=config.TAG_STRATEGIES.index(
                        ss.get("tag_strategy", config.DEFAULT_TAG_STRATEGY)),
                    help="Как мёржить теги. Применяется при «Полной регенерации» "
                         "(и при защите «только теги»). В режиме «Дополнить» модель "
                         "сама решает — её ответ пишется как есть.",
                )
            with uc2:
                ss.prose_strategy = st.selectbox(
                    "Проза", config.PROSE_STRATEGIES,
                    index=config.PROSE_STRATEGIES.index(
                        ss.get("prose_strategy", config.DEFAULT_PROSE_STRATEGY)),
                    help="Как поступать с прозой. Применяется при «Полной "
                         "регенерации». В режиме «Дополнить» проза берётся из ответа "
                         "модели (она видела старый капшен).",
                )
                ss.manual_policy = st.selectbox(
                    "Ручные правки", config.MANUAL_POLICIES,
                    index=config.MANUAL_POLICIES.index(
                        ss.get("manual_policy", config.DEFAULT_MANUAL_POLICY)),
                    help="Что делать с капшенами, которые редактировал "
                         "человек после генерации.",
                )
            st.markdown("**Фильтры — какие файлы обновлять:**")
            fc1, fc2, fc3, fc4 = st.columns(4)
            with fc1:
                ss.upd_filter_prompt = st.checkbox("Устаревший промпт",
                                                   ss.get("upd_filter_prompt", True))
            with fc2:
                ss.upd_filter_model = st.checkbox("Сменилась модель",
                                                  ss.get("upd_filter_model", False))
            with fc3:
                ss.upd_filter_quality = st.checkbox("Плохое качество",
                                                    ss.get("upd_filter_quality", False))
            with fc4:
                ss.upd_filter_all = st.checkbox("Все файлы",
                                                ss.get("upd_filter_all", False))

    if st.button("🔍 Сканировать"):
        if os.path.isdir(ss.folder):
            ss.scan_info = scan_summary(ss.folder, ss.recursive)
            reg = get_registry()
            imgs = find_images(ss.folder, ss.recursive)
            ss.scan_info["done_by_app"] = sum(1 for p in imgs if reg.is_done(p))
        else:
            ss.scan_info = None
            st.error("Папка не найдена")

    if ss.scan_info:
        s = ss.scan_info
        st.info(f"Найдено изображений: **{s['total']}**  ·  "
                f"с капшенами: **{s['with_caption']}**  ·  "
                f"без капшенов: **{s['missing']}**  ·  "
                f"сделано этим приложением: **{s.get('done_by_app', 0)}**")

    # --- Пресеты и промпты ---
    st.subheader("2. Пресет и промпты")
    preset_names = list(ss.presets.keys())
    col_p1, col_p2, col_p3 = st.columns([3, 1, 1])
    with col_p1:
        chosen = st.selectbox("Пресет", preset_names,
                              index=preset_names.index(ss.preset_name)
                              if ss.preset_name in preset_names else 0)
        if chosen != ss.preset_name:
            ss.preset_name = chosen
            ss.system_prompt = ss.presets[chosen]["system"]
            ss.user_prompt = ss.presets[chosen]["user"]
            st.rerun()

    ss.system_prompt = st.text_area("System Prompt", ss.system_prompt, height=90)
    ss.user_prompt = st.text_area("User Prompt", ss.user_prompt, height=200)
    ss.trigger_word = st.text_input(
        "Триггер-слово стиля (style-LoRA)",
        ss.trigger_word,
        help="Подставляется ПЕРВЫМ тегом в каждый .txt, одинаково во всём датасете. "
             "Модель его не пишет (перевирала бы) — добавляем программно при записи. "
             "Стиль оседает в этом слове; поэтому промт НЕ описывает стиль. "
             "Пусто = не добавлять.",
    )

    col_sp1, col_sp2, col_sp3 = st.columns([2, 1, 1])
    with col_sp1:
        new_preset_name = st.text_input("Имя пресета для сохранения", ss.preset_name)
    with col_sp2:
        st.write("")
        st.write("")
        if st.button("💾 Сохранить пресет", width="stretch"):
            try:
                presets_mod.save_preset(new_preset_name, ss.system_prompt, ss.user_prompt)
                ss.presets = presets_mod.load_presets()
                ss.preset_name = new_preset_name
                st.success(f"Пресет «{new_preset_name}» сохранён")
            except ValueError as e:
                st.error(str(e))
    with col_sp3:
        st.write("")
        st.write("")
        if st.button("🗑️ Удалить пресет", width="stretch"):
            if presets_mod.delete_preset(ss.preset_name):
                ss.presets = presets_mod.load_presets()
                st.success("Пресет удалён")
                st.rerun()
            else:
                st.warning("Встроенный пресет удалить нельзя")

    # ----------------------------------------------------------------------- #
    # Управление обработкой
    # ----------------------------------------------------------------------- #
    st.subheader("3. Обработка")

    snap = worker.snapshot()
    running = snap["running"]
    paused = snap["paused"]
    has_review = snap["has_review"]

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        start_clicked = st.button("▶️ Запустить", width="stretch",
                                  disabled=running)
    with c2:
        pause_clicked = st.button("⏸️ Пауза", width="stretch",
                                  disabled=not running or paused or has_review)
    with c3:
        resume_clicked = st.button("⏵️ Возобновить", width="stretch",
                                   disabled=not running or not paused)
    with c4:
        stop_clicked = st.button("⏹️ Остановить", width="stretch",
                                 disabled=not running)

    if start_clicked:
        ss._notified = False
        if not os.path.isdir(ss.folder):
            st.error("Укажите существующую папку")
        elif ss.mode == config.MODE_UPDATE:
            registry = get_registry()
            cur_hash = prompt_signature(ss.system_prompt, ss.user_prompt)
            filters = {
                "prompt_changed": ss.get("upd_filter_prompt", True),
                "model_changed": ss.get("upd_filter_model", False),
                "quality": ss.get("upd_filter_quality", False),
                "all": ss.get("upd_filter_all", False),
            }
            update_tasks = build_update_plan(
                ss.folder, ss.recursive, registry,
                current_prompt_hash=cur_hash,
                current_model=ss.model,
                filters=filters,
            )
            if not update_tasks:
                st.warning("Нет файлов для обновления по выбранным фильтрам")
            else:
                manual_count = sum(1 for t in update_tasks if t.manually_edited)
                logger().info(f"Старт обновления: {len(update_tasks)} файлов "
                              f"(из них ручных правок: {manual_count})")
                worker.start_update(update_tasks, ss.folder, get_params(),
                                    logger(), registry, get_client())
                st.rerun()
        else:
            tasks = build_task_list(ss.folder, ss.recursive, ss.mode, get_registry())
            if not tasks:
                st.warning("Нет файлов для обработки в выбранном режиме")
            else:
                logger().info(f"Старт обработки: {len(tasks)} файлов, режим «{ss.mode}»")
                worker.start(tasks, ss.folder, get_params(), logger(),
                             get_registry(), get_client())
                st.rerun()

    if pause_clicked:
        worker.pause()          # мгновенный обрыв генерации на сервере + удержание
        st.rerun()

    if resume_clicked:
        worker.resume()
        st.rerun()

    if stop_clicked:
        worker.stop()           # обрыв генерации + завершение потока + сохранение
        st.rerun()

    # --- Прогресс и статистика ---
    # clamp в [0,1]: при рассинхроне сохранённого прогресса (index > len(tasks))
    # отношение может выйти за 1.0 и уронить st.progress — не даём этому случиться.
    if snap["update_total"] > 0:
        _ratio = snap["update_done"] / snap["update_total"]
        st.progress(min(1.0, max(0.0, _ratio)))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Всего", snap["update_total"])
        m2.metric("Обновлено", snap["update_done"] - snap["update_skipped"] - snap["update_errors"])
        m3.metric("Пропущено", snap["update_skipped"])
        m4.metric("Ошибок", snap["update_errors"])
    else:
        _ratio = snap["done"] / snap["total"] if snap["total"] else 0.0
        st.progress(min(1.0, max(0.0, _ratio)))
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Всего", snap["total"])
        m2.metric("Обработано", snap["processed"])
        m3.metric("Пропущено", snap["skipped"])
        m4.metric("Ошибок", snap["errors"])
        m5.metric("Готово", f"{snap['done']}/{snap['total']}")

    # ETA и скорость
    _done_n = snap["update_done"] if snap["update_total"] > 0 else snap["done"]
    _total_n = snap["update_total"] if snap["update_total"] > 0 else snap["total"]
    _elapsed = time.time() - snap["start_ts"] if snap["start_ts"] else 0.0
    if running and _done_n > 0 and _elapsed > 5:
        _avg = _elapsed / _done_n
        _remaining = (_total_n - _done_n) * _avg
        ec = st.columns(3)
        ec[0].metric("Скорость", f"{_avg:.0f} с/файл")
        ec[1].metric("Прошло", fmt_duration(_elapsed))
        ec[2].metric("Осталось", fmt_duration(_remaining))

    status_placeholder = st.empty()
    preview_col, caption_col = st.columns(2)

    # --- Отображение состояния + polling ---
    poll = False
    if has_review:
        review = worker.get_review()
        if review is not None:
            render_review(review, preview_col, caption_col)
        else:
            poll = True  # ревью уже закрылось — обновимся в конце
    elif running:
        status_placeholder.info(snap["status_msg"])
        poll = True
    elif snap["finished"]:
        status_placeholder.success(snap["status_msg"] or "✅ Обработка завершена")
        if ss.get("notify_on_finish") and not ss.get("_notified"):
            import streamlit.components.v1 as stc
            stc.html(
                '<script>'
                'if(Notification.permission==="granted")'
                '  new Notification("Tag Manager",{body:"Обработка завершена!"});'
                'else if(Notification.permission!=="denied")'
                '  Notification.requestPermission();'
                '</script>',
                height=0,
            )
            ss._notified = True
        # Диффы обновления
        _diffs = snap.get("update_diffs", [])
        if _diffs:
            # В ключ виджета вплетаем таймстемп прогона: без него при повторном
            # обновлении тех же файлов key совпал бы с прошлым разом и text_area
            # показал бы залипшее старое значение (Streamlit игнорирует value у
            # keyed-виджета). Нонс = свежая идентичность виджета на каждый прогон.
            _nonce = int(snap.get("start_ts", 0))
            with st.expander(f"📝 Что изменилось ({len(_diffs)} файлов)"):
                for _di, (_dn, _old, _new) in enumerate(_diffs[:30]):
                    st.markdown(f"**{_dn}**")
                    _dc = st.columns(2)
                    _dc[0].text_area("до", _old, height=120, disabled=True,
                                     key=f"diff_old_{_nonce}_{_di}")
                    _dc[1].text_area("после", _new, height=120, disabled=True,
                                     key=f"diff_new_{_nonce}_{_di}")
        # Отложенные на ручной просмотр (политика «Отложить»). Показываем список,
        # иначе выбор этой политики был бы невидим — файлы копятся в скрытом JSON.
        if snap.get("update_deferred", 0) and ss.folder:
            _rev_path = os.path.join(ss.folder, config.DEFERRED_REVIEW_FILE)
            _deferred: list[str] = []
            if os.path.isfile(_rev_path):
                try:
                    import json as _json
                    with open(_rev_path, encoding="utf-8") as _rf:
                        _deferred = _json.load(_rf)
                except (OSError, ValueError):
                    _deferred = []
            with st.expander(f"🔎 Отложено на ручной просмотр ({len(_deferred)})"):
                st.caption("Эти капшены правил человек — обновление их не трогало. "
                           "Проверьте вручную (список в "
                           f"`{config.DEFERRED_REVIEW_FILE}`).")
                for _dp in _deferred[:100]:
                    st.text(os.path.basename(_dp))
    else:
        status_placeholder.write(snap["status_msg"] or "Готов к запуску.")

    # --- Реал-тайм лог ---
    st.subheader("4. Лог")
    st.text_area("processing_log", logger().get_text(), height=220, key="log_view")

    return poll
