"""Сайдбар: настройки API, чекбоксы поведения, стоп-лист, прогресс, экспорт.

Автосохранение «липких» настроек (settings.json) происходит здесь на каждом
ререндере. Кнопки прогресса дёргают воркер (продолжить/сбросить прошлый прогон).
"""

from __future__ import annotations

import os

import streamlit as st

import config
from core import app_settings
from core.inference.installer import LlamaCppInstaller, discover_gguf
from core.models.library import clear_model_library_cache, scan_model_library
from core.inference.profiles import capture_profile, save_backend_profiles
from core.diagnostics import export_diagnostics
from ui.context import get_client, get_params, get_registry, logger


def render_sidebar() -> None:
    ss = st.session_state
    worker = ss.worker
    proc = worker.state

    with st.sidebar:
        st.header("Дополнительно")
        ss.setdefault("show_advanced", False)
        ss.show_advanced = st.toggle(
            "Расширенные настройки",
            value=bool(ss.show_advanced),
            help="Ручная настройка модели, ресурсов и восстановления.",
        )
        if not ss.show_advanced:
            st.caption("Для обычной работы ручная настройка не требуется.")
            return

        st.subheader("Запуск модели")
        backend_labels = {
            "external": "Внешний сервер с совместимым API",
            "managed_llama": "Встроенный запуск llama.cpp",
        }
        ss.backend_type = st.selectbox(
            "Способ запуска",
            list(backend_labels),
            index=list(backend_labels).index(ss.backend_type),
            format_func=backend_labels.get,
            disabled=worker.is_alive(),
        )
        ss.disable_thinking = st.checkbox(
            "Отключить размышления (thinking)",
            value=ss.disable_thinking,
            help="Меняет не только prompt/API-флаги, но и профиль llama.cpp: "
                 "без thinking используется быстрый context; с thinking — "
                 "увеличенный context и quantized KV cache.",
        )
        reasoning_mode = not ss.disable_thinking
        if ss.get("llama_reasoning_mode") != reasoning_mode:
            ss.llama_reasoning_mode = reasoning_mode
            ss.max_tokens = 6144 if reasoning_mode else min(int(ss.max_tokens), 4096)
        reasoning_budget_value = st.number_input(
            "Максимум токенов reasoning",
            min_value=0, max_value=65536,
            value=int(ss.llama_reasoning_budget if reasoning_mode else 0),
            step=256, disabled=not reasoning_mode,
            help="llama.cpp --reasoning-budget: 0 завершает thinking сразу, "
                 "положительное значение ограничивает его длину.",
        )
        if reasoning_mode:
            ss.llama_reasoning_budget = reasoning_budget_value
        if ss.backend_type == "managed_llama":
            installer = LlamaCppInstaller(
                config.LLAMA_VERSIONS_DIR, config.DEFAULT_LLAMA_BUILD
            )
            installed_executable = installer.current_executable()
            if installed_executable:
                ss.llama_executable = installed_executable
            elif not ss.get("llama_auto_install_attempted", False):
                ss.llama_auto_install_attempted = True
                progress_bar = st.progress(0.0, "Подготовка llama.cpp…")

                def install_progress(done: int, total: int, asset: str) -> None:
                    progress_bar.progress(
                        min(1.0, done / max(1, total)),
                        f"Скачивание {asset}: {done / (1 << 20):.0f} / "
                        f"{total / (1 << 20):.0f} МБ",
                    )

                try:
                    ss.llama_executable = installer.ensure_latest(install_progress)
                    progress_bar.empty()
                    st.success("Последняя версия llama.cpp установлена автоматически")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    progress_bar.empty()
                    ss.llama_install_error = str(exc)
            if ss.get("llama_install_error"):
                st.error(f"Не удалось установить llama.cpp: {ss.llama_install_error}")
                if st.button("Повторить установку llama.cpp", width="stretch"):
                    ss.llama_auto_install_attempted = False
                    ss.llama_install_error = ""
                    st.rerun()
            elif ss.llama_executable:
                st.caption(
                    f"llama.cpp установлен: {os.path.basename(os.path.dirname(ss.llama_executable))}"
                )

            ss.model_directory = st.text_input(
                "Папка моделей", ss.model_directory,
                help="Папка сканируется автоматически; вручную вводить имя GGUF не нужно.",
            )
            if st.button("Обновить список моделей", key="refresh_advanced_model_library",
                         width="stretch"):
                clear_model_library_cache()
                st.rerun()
            library = scan_model_library(
                ss.model_directory, mmproj_roots=(ss.mmproj_directory,)
            )
            models = [entry.path for entry in library.entries]
            if ss.llama_model and ss.llama_model not in models and os.path.isfile(ss.llama_model):
                models.insert(0, ss.llama_model)
            if models:
                current_model = ss.llama_model if ss.llama_model in models else models[0]
                ss.llama_model = st.selectbox(
                    "GGUF-модель", models, index=models.index(current_model),
                    format_func=os.path.basename,
                )
            else:
                ss.llama_model = ""
                st.warning("В указанной папке не найдено GGUF-моделей")

            ss.mmproj_directory = st.text_input(
                "Папка vision/mmproj", ss.mmproj_directory
            )
            mmproj_files = discover_gguf(ss.mmproj_directory)
            mmproj_options = [""] + mmproj_files
            current_mmproj = (
                ss.llama_mmproj if ss.llama_mmproj in mmproj_options else
                (mmproj_files[0] if len(mmproj_files) == 1 else "")
            )
            ss.llama_mmproj = st.selectbox(
                "Vision projector", mmproj_options,
                index=mmproj_options.index(current_mmproj),
                format_func=lambda path: os.path.basename(path) if path else "Без mmproj",
            )
            selected_entry = next(
                (entry for entry in library.entries if entry.path == ss.llama_model), None
            )
            if selected_entry:
                metadata = selected_entry.metadata
                st.caption(
                    f"{metadata.architecture or 'unknown'} · "
                    f"{metadata.quantization} · "
                    f"{metadata.block_count or '?'} blocks · "
                    f"context {metadata.context_length or '?'} · "
                    f"MoE {metadata.expert_used_count or '-'} / {metadata.expert_count or '-'}"
                )
                if selected_entry.warnings:
                    for warning in selected_entry.warnings:
                        st.warning(warning)
            host_col, port_col = st.columns([2, 1])
            with host_col:
                ss.llama_host = st.text_input("Host", ss.llama_host)
            with port_col:
                ss.llama_port = st.number_input(
                    "Port", 1, 65535, int(ss.llama_port)
                )
            ss.llama_api_prefix = st.text_input(
                "API prefix", ss.llama_api_prefix
            )
            ss.llama_startup_timeout = st.number_input(
                "Таймаут запуска (сек)", 5, 1800,
                int(ss.llama_startup_timeout), 5,
            )
            with st.expander("Профили backend"):
                profile_names = list(ss.backend_profiles)
                selected_profile = st.selectbox(
                    "Сохранённый профиль",
                    [""] + profile_names,
                    format_func=lambda name: name or "Не выбран",
                )
                p_load, p_delete = st.columns(2)
                with p_load:
                    if st.button("Загрузить", width="stretch",
                                 disabled=not selected_profile or worker.is_alive()):
                        for key, value in ss.backend_profiles[selected_profile].items():
                            ss[key] = value
                        st.rerun()
                with p_delete:
                    if st.button("Удалить", width="stretch",
                                 disabled=not selected_profile or worker.is_alive()):
                        ss.backend_profiles.pop(selected_profile, None)
                        save_backend_profiles(ss.backend_profiles)
                        st.rerun()
            ss.llama_optimization_mode = st.segmented_control(
                "Настройка производительности",
                options=["auto", "prefer_vram", "balanced", "prefer_ram", "manual"],
                default=ss.llama_optimization_mode,
                format_func=lambda value: {
                    "auto": "Авто", "prefer_vram": "VRAM",
                    "balanced": "Баланс", "prefer_ram": "RAM",
                    "manual": "Вручную",
                }[value],
                selection_mode="single",
            ) or "auto"
            if ss.llama_optimization_mode == "manual":
                kv_types = [
                    "f32", "f16", "bf16", "q8_0", "q5_0", "q5_1",
                    "q4_0", "q4_1", "iq4_nl",
                ]
                with st.expander("Расширенные параметры llama.cpp", expanded=True):
                    ss.llama_context_size = st.number_input(
                        "Context size", 512, 262144,
                        int(ss.llama_context_size), 512,
                    )
                    ss.llama_gpu_layers = st.text_input(
                        "GPU layers", str(ss.llama_gpu_layers),
                        help="auto, all или точное целое число.",
                    ).strip()
                    ss.llama_fit_target = st.number_input(
                        "Резерв VRAM для auto-fit (MiB)", 0, 32768,
                        int(ss.llama_fit_target), 128,
                    )
                    c1, c2 = st.columns(2)
                    with c1:
                        ss.llama_slots = st.number_input(
                            "Server slots", 1, 32, int(ss.llama_slots)
                        )
                        ss.llama_threads = st.number_input(
                            "CPU threads (0 = auto)", 0, 256,
                            int(ss.llama_threads)
                        )
                        ss.llama_batch = st.selectbox(
                            "Batch size", [128, 256, 512, 1024, 2048, 4096],
                            index=[128, 256, 512, 1024, 2048, 4096].index(
                                int(ss.llama_batch)
                            ),
                        )
                    with c2:
                        ss.llama_ubatch = st.selectbox(
                            "Ubatch size", [64, 128, 256, 512, 1024, 2048],
                            index=[64, 128, 256, 512, 1024, 2048].index(
                                int(ss.llama_ubatch)
                            ),
                        )
                        ss.llama_flash_attn = st.selectbox(
                            "Flash Attention", ["auto", "on", "off"],
                            index=["auto", "on", "off"].index(ss.llama_flash_attn),
                        )
                        load_modes = ["mmap", "no-mmap", "mlock", "mmap+mlock", "dio"]
                        ss.llama_load_mode = st.selectbox(
                            "Load mode", load_modes,
                            index=load_modes.index(ss.llama_load_mode),
                        )
                    kcol, vcol = st.columns(2)
                    with kcol:
                        ss.llama_cache_k = st.selectbox(
                            "KV cache K", kv_types,
                            index=kv_types.index(ss.llama_cache_k),
                        )
                    with vcol:
                        ss.llama_cache_v = st.selectbox(
                            "KV cache V", kv_types,
                            index=kv_types.index(ss.llama_cache_v),
                        )
            with st.expander("Сохранить профиль backend"):
                default_profile_name = (
                    os.path.splitext(os.path.basename(ss.llama_model))[0]
                    + (" · reasoning" if not ss.disable_thinking else " · fast")
                ).strip(" ·")
                ss.backend_profile_name = st.text_input(
                    "Имя профиля", ss.backend_profile_name or default_profile_name
                )
                if st.button("Сохранить текущие настройки", width="stretch",
                             disabled=not ss.backend_profile_name.strip()):
                    ss.backend_profiles[ss.backend_profile_name.strip()] = capture_profile(ss)
                    save_backend_profiles(ss.backend_profiles)
                    st.toast("Профиль backend сохранён")
            managed = get_client()
            health = managed.health()
            st.caption(f"Статус: {health.status.value} — {health.message}")
            profile = ss.get("llama_optimization_profile")
            if profile:
                st.caption(
                    f"Профиль: {profile.name} · context {profile.context_size} · "
                    f"max output {profile.max_output_tokens}"
                )
                with st.expander("Почему выбраны эти настройки"):
                    for reason in profile.explanation:
                        st.write(f"• {reason}")
            compatibility = ss.get("llama_compatibility_report")
            if compatibility:
                budget = compatibility.budget
                message = (
                    f"{compatibility.summary}: требуется ~{budget.required_bytes / (1 << 30):.1f} GiB, "
                    f"доступно ~{budget.available_bytes / (1 << 30):.1f} GiB"
                )
                if compatibility.severity == "error":
                    st.error(message)
                elif compatibility.severity == "warning":
                    st.warning(message)
                else:
                    st.success(message)
            active_model_path = health.details.get("model_path")
            requested_args = profile.args if profile else config.DEFAULT_LLAMA_EXTRA_ARGS
            active_args = tuple(health.details.get("extra_args") or ())
            if health.ready and (
                active_model_path != ss.llama_model or active_args != requested_args
            ):
                st.info("Изменения конфигурации применятся после остановки сервера.")
            start_col, stop_col = st.columns(2)
            with start_col:
                if st.button("▶ Start llama.cpp", width="stretch",
                             disabled=(worker.is_alive() or health.ready
                                       or not ss.llama_executable or not ss.llama_model
                                       or bool(compatibility and not compatibility.compatible))):
                    try:
                        ready = managed.start()
                        st.success(ready.message)
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
            with stop_col:
                if st.button("■ Stop llama.cpp", width="stretch",
                             disabled=worker.is_alive() or not health.ready):
                    managed.stop()
                    st.rerun()
            ss.model = os.path.basename(ss.llama_model) or "local-model"
        else:
            ss.api_url = st.text_input("API URL", ss.api_url)
        mcol1, mcol2 = st.columns([4, 1])
        with mcol1:
            ss.model = st.text_input("Модель", ss.model,
                                     help="Заполняется активной моделью сервера. "
                                          "🔄 — обновить с текущего API URL.")
        with mcol2:
            st.write("")
            st.write("")
            if st.button("🔄", width="stretch", help="Подтянуть активную модель с сервера",
                         disabled=ss.backend_type == "managed_llama"):
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
        ss.notify_on_finish = st.checkbox(
            "Уведомлять о завершении",
            value=ss.notify_on_finish,
            help="Браузерное уведомление (Web Notification) когда прогон завершён. "
                 "Удобно, если ушли от вкладки на время обработки.",
        )
        ss.caption_edit_height = st.slider(
            "Высота полей правки капшена (px)", 120, 800,
            int(ss.get("caption_edit_height", config.DEFAULT_CAPTION_EDIT_HEIGHT)), 20,
            help="Высота текстовых полей редактора капшена в ручном ревью и галерее. "
                 "Задаётся один раз и запоминается между сессиями (перетаскивание "
                 "угла поля мышью Streamlit не сохраняет).",
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
        if st.button("🧾 Экспорт diagnostics", width="stretch"):
            health_snapshot = get_client().health()
            path = export_diagnostics(
                config.DIAGNOSTICS_FILE,
                worker_snapshot=worker.snapshot(),
                backend_health=health_snapshot,
                profile=ss.get("llama_optimization_profile"),
                compatibility=ss.get("llama_compatibility_report"),
            )
            st.success(f"Diagnostics сохранён: {path}")

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
