"""Сайдбар: настройки API, чекбоксы поведения, стоп-лист, прогресс, экспорт.

Автосохранение «липких» настроек (settings.json) происходит здесь на каждом
ререндере. Кнопки прогресса дёргают воркер (продолжить/сбросить прошлый прогон).
"""

from __future__ import annotations

import os

import streamlit as st

import config
from core import app_settings
from ui.context import get_client, get_params, get_registry, logger


def render_sidebar() -> None:
    ss = st.session_state
    worker = ss.worker
    proc = worker.state

    with st.sidebar:
        st.header("⚙️ Настройки API")
        ss.api_url = st.text_input("API URL", ss.api_url)
        mcol1, mcol2 = st.columns([4, 1])
        with mcol1:
            ss.model = st.text_input("Модель", ss.model,
                                     help="Заполняется активной моделью сервера. "
                                          "🔄 — обновить с текущего API URL.")
        with mcol2:
            st.write("")
            st.write("")
            if st.button("🔄", width="stretch", help="Подтянуть активную модель с сервера"):
                detected = get_client().active_model()
                if detected:
                    ss.model = detected
                    st.toast(f"Модель: {detected}")
                    st.rerun()
                else:
                    st.toast("Сервер недоступен — модель не получена")
        ss.temperature = st.slider("Temperature", 0.0, 2.0, float(ss.temperature), 0.05)
        ss.top_p = st.slider("Top-p", 0.0, 1.0, float(ss.top_p), 0.05)
        ss.max_tokens = st.number_input("Max tokens (для thinking-моделей ставьте больше)",
                                        16, 16384, int(ss.max_tokens), 128)
        ss.timeout = st.number_input("Таймаут запроса (сек)", 30, 1800, int(ss.timeout), 30)
        ss.auto_retry = st.checkbox(
            "Авто-ретрай при плохом капшене",
            value=ss.auto_retry,
            help="Перегенерировать, если капшен слишком короткий / только теги / с залипанием. "
                 "Каждый повтор ~10 мин. Выключите, если теговый стиль вас устраивает.",
        )
        ss.manual_review = st.checkbox(
            "Проверять каждый капшен вручную",
            value=ss.manual_review,
            help="После генерации каждого файла обработка приостановится и покажет капшен "
                 "для ручного решения: принять / правка / перегенерировать / пропустить.",
        )
        ss.disable_thinking = st.checkbox(
            "Отключить размышления (thinking)",
            value=ss.disable_thinking,
            help="Просит модель не выводить блок рассуждений: сильно ускоряет генерацию "
                 "и убирает пустые ответы «лимит ушёл на размышления». Шлём известные "
                 "серверу переключатели (enable_thinking=false, reasoning_budget=0) и "
                 "текстовые маркеры для Qwen/Gemma. Модель без thinking просто игнорирует.",
        )
        ss.notify_on_finish = st.checkbox(
            "Уведомлять о завершении",
            value=ss.notify_on_finish,
            help="Браузерное уведомление (Web Notification) когда прогон завершён. "
                 "Удобно, если ушли от вкладки на время обработки.",
        )

        # Автосохранение «липких» настроек: любое изменение полей выше пишем в
        # settings.json, чтобы при следующем запуске они восстановились сами.
        app_settings.save_settings({k: ss[k] for k in app_settings.PERSISTED_KEYS if k in ss})

        with st.expander("Стоп-лист тегов"):
            from core.stoplist import load_stoplist as _load_sl, save_stoplist as _save_sl
            _sl_path = config.STOPLIST_FILE
            _sl_current = ""
            if os.path.isfile(_sl_path):
                try:
                    with open(_sl_path, encoding="utf-8") as _f:
                        _sl_current = _f.read()
                except OSError:
                    pass
            _sl_edited = st.text_area(
                "Один тег на строку, # = комментарий",
                _sl_current, height=120, key="stoplist_edit",
            )
            _sl_tags = _load_sl(_sl_path)
            st.caption(f"Тегов в стоп-листе: {len(_sl_tags)}")
            if st.button("Сохранить стоп-лист", width="stretch"):
                _save_sl(_sl_edited, _sl_path)
                st.toast("Стоп-лист сохранён")

        if st.button("🔌 Проверить соединение", width="stretch"):
            ok, msg = get_client().check_connection()
            (st.success if ok else st.error)(msg)
            logger().info(f"Проверка соединения: {msg}")

        st.divider()
        st.caption("Прогресс")
        if st.button("💾 Продолжить прошлый прогон", width="stretch",
                     disabled=worker.is_alive()):
            if proc.load_progress():
                # Восстанавливаем папку и СРАЗУ продолжаем цикл с сохранённого места
                # в фоновом воркере (▶️ Запустить пересобрал бы список с нуля).
                ss.folder = proc.folder
                if not proc.is_finished():
                    worker.start_resumed(get_params(), logger(), get_registry(), get_client())
                st.success(f"Восстановлено: {proc.done_count}/{proc.total} — продолжаю")
                st.rerun()
            else:
                st.warning("Сохранённый прогресс не найден")
        if st.button("🗑️ Сбросить прогресс", width="stretch",
                     disabled=worker.is_alive()):
            proc.clear_progress()
            st.info("Файл прогресса удалён")

        st.divider()
        with st.expander("📥 Экспорт конфига для тренера"):
            from core.export import export_kohya_toml, export_onetrainer
            _exp_fmt = st.selectbox("Формат", ["OneTrainer (JSON)", "kohya (TOML)"],
                                    key="export_fmt")
            _exp_rep = st.number_input("Repeats", 1, 100, 10, key="export_rep")
            _exp_res = st.number_input("Resolution", 256, 2048, 512, 64,
                                       key="export_res")
            if ss.folder and os.path.isdir(ss.folder):
                if "OneTrainer" in _exp_fmt:
                    _data = export_onetrainer(ss.folder, ss.trigger_word,
                                              _exp_rep, _exp_res)
                    _fname = "dataset.json"
                else:
                    _data = export_kohya_toml(ss.folder, ss.trigger_word,
                                              _exp_rep, _exp_res)
                    _fname = "dataset.toml"
                st.download_button("📥 Скачать конфиг", _data, _fname,
                                   width="stretch")
            else:
                st.caption("Укажите папку на вкладке «Генерация».")
