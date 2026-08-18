"""Пользовательская рабочая область: контекст dataset/run и следующий шаг.

Это тонкий UX-слой над существующими вкладками. Он не дублирует операции и не
трогает core, а даёт пользователю единую точку ориентации.
"""

from __future__ import annotations

import os
import time

import streamlit as st
import config

from core import app_settings
from core.image_scanner import find_images, scan_summary
from core.inference.installer import discover_gguf
from core.models.library import clear_model_library_cache, scan_model_library
from ui.common import browse_into, fmt_duration
from ui.context import get_registry
from ui.context import get_client, get_params
from ui.viewmodels import sync_dataset_context, workspace_view
from ui.design import mode_banner, section_heading


def _render_active_run(snapshot: dict) -> None:
    """Focused run cockpit: only live information and immediate actions."""
    ss = st.session_state
    worker = ss.worker
    total = int(snapshot.get("update_total") or snapshot.get("total") or 0)
    done = int(snapshot.get("update_done") or snapshot.get("done") or 0)
    elapsed = float(snapshot.get("elapsed_seconds") or 0.0)
    average = elapsed / done if done else 0.0
    remaining = max(0, total - done) * average if average else 0.0

    section_heading(
        "ТРЕБУЕТСЯ РЕШЕНИЕ" if snapshot.get("has_review") else "ВЫПОЛНЯЕТСЯ",
        "Проверьте текущую подпись" if snapshot.get("has_review") else "Генерация подписей",
        "Настройки зафиксированы до конца запуска. Здесь остаётся только ход работы и управление.",
    )

    stage = "Пауза" if snapshot.get("paused") else (
        "Ожидает проверки" if snapshot.get("has_review") else "Генерация"
    )
    metrics = st.columns(5)
    metrics[0].metric("Этап", stage)
    metrics[1].metric("Готово", f"{done} / {total}" if total else "—")
    metrics[2].metric("Ошибки", int(snapshot.get("errors", 0) or 0))
    metrics[3].metric("Прошло", fmt_duration(elapsed))
    metrics[4].metric("Осталось", fmt_duration(remaining) if remaining else "—")
    st.progress(done / total if total else 0.0, text=f"Общий прогресс · {done} из {total}")

    current = worker.state.current_task()
    if snapshot.get("has_review"):
        from ui.generation import render_review
        review = worker.get_review()
        if review is not None:
            preview_col, caption_col = st.columns([2, 3])
            render_review(review, preview_col, caption_col)
    else:
        visual, activity = st.columns([2, 3])
        with visual:
            if current and os.path.isfile(current.image_path):
                st.image(current.image_path, width="stretch")
            else:
                st.info("Изображение подготавливается")
        with activity:
            st.markdown("### Сейчас")
            st.markdown(f"**{snapshot.get('current_name') or 'Подготовка первого файла'}**")
            st.info(snapshot.get("status_msg") or "Подготовка…")
            if average:
                st.caption(f"Среднее время: {fmt_duration(average)} на изображение")
            st.caption(f"Запуск: {snapshot.get('run_id') or '—'}")

    controls = st.columns([1, 1, 1, 3])
    if controls[0].button("Продолжить", width="stretch",
                          disabled=not snapshot.get("paused")):
        worker.resume()
        st.rerun()
    if controls[1].button("Пауза", width="stretch",
                          disabled=snapshot.get("paused") or snapshot.get("has_review")):
        worker.pause()
        st.rerun()
    if controls[2].button("Остановить", width="stretch"):
        worker.stop()
        st.rerun()
    controls[3].caption(
        "Пауза прерывает текущую генерацию; после продолжения этот файл будет создан заново."
    )


def _render_finished_run(snapshot: dict) -> None:
    """Completion state with outcome and next actions, not stale setup controls."""
    ss = st.session_state
    errors = int(snapshot.get("errors", 0) or 0)
    total = int(snapshot.get("update_total") or snapshot.get("total") or 0)
    done = int(snapshot.get("update_done") or snapshot.get("done") or 0)
    section_heading(
        "ЗАВЕРШЕНО С ОШИБКАМИ" if errors else "ГОТОВО",
        "Обработка завершена",
        "Проверьте результат или подготовьте следующий запуск.",
    )
    summary = st.columns(4)
    summary[0].metric("Обработано", done)
    summary[1].metric("Всего", total)
    summary[2].metric("Пропущено", int(snapshot.get("skipped", 0) or 0))
    summary[3].metric("Ошибки", errors)
    if errors:
        st.warning("Часть файлов не обработана. Откройте результаты, чтобы найти проблемные элементы.")
    else:
        st.success("Подписи созданы. Рекомендуется быстро проверить результат перед обучением.")
    actions = st.columns(3)
    if actions[0].button("Проверить результаты", type="primary", width="stretch"):
        ss.main_page = "Результаты"
        st.rerun()
    if actions[1].button("Посмотреть запуск", width="stretch"):
        ss.main_page = "Запуски"
        st.rerun()
    if actions[2].button("Настроить новый запуск", width="stretch"):
        ss.show_setup_after_finish = True
        st.rerun()

def render_workspace() -> None:
    ss = st.session_state
    worker = ss.worker
    snapshot = worker.snapshot()
    folder = ss.get("folder", "")
    view = workspace_view(folder, ss.get("scan_info"), snapshot)

    if snapshot.get("running"):
        _render_active_run(snapshot)
        return
    if snapshot.get("finished") and not ss.get("show_setup_after_finish", False):
        _render_finished_run(snapshot)
        return
    mode_labels = {
        config.MODE_ONLY_MISSING: "Продолжить: заполнить пропуски",
        config.MODE_RESUME: "Докачать по реестру Tag Manager",
        config.MODE_UPDATE: "Обновить существующие подписи",
        config.MODE_ALL: "Пересоздать все подписи",
        config.MODE_SKIP_PROCESSED: "Обработать изменённые файлы",
    }
    pipeline_labels = {
        "description_only": "Описание",
        "tags_only": "Только теги",
        "tags_and_description": "Теги и описание",
        "tags_to_vlm_context": "Теги помогают описанию",
    }

    section_heading(
        "ВЫПОЛНЯЕТСЯ" if snapshot.get("running") else "РАБОТА",
        "Текущий запуск" if snapshot.get("running") else "Подготовьте запуск",
    )
    ss.setdefault("workspace_folder", folder)
    ss.setdefault("workspace_recursive", bool(ss.get("recursive", False)))
    pick = st.columns([5, 1, 1], vertical_alignment="bottom")
    selected = pick[0].text_input("Папка с изображениями", key="workspace_folder",
                                  disabled=not view.can_change_dataset)
    pick[1].button(
        "Обзор", width="stretch", disabled=not view.can_change_dataset,
        on_click=browse_into, args=("workspace_folder",),
    )
    recursive = pick[2].checkbox("Подпапки", key="workspace_recursive",
                                 disabled=not view.can_change_dataset)
    if (selected != folder or recursive != bool(ss.get("recursive", False))) and view.can_change_dataset:
        sync_dataset_context(ss, selected, recursive)
        ss.scan_info = None

    if st.button("Проверить папку", disabled=not os.path.isdir(selected)):
        info = scan_summary(selected, recursive)
        registry = get_registry()
        images = find_images(selected, recursive)
        info["done_by_app"] = sum(1 for path in images if registry.is_done(path))
        ss.scan_info = info
        sync_dataset_context(ss, selected, recursive)
        app_settings.save_settings(dict(ss))
        st.rerun()

    st.markdown("### Что сделать")
    intent_col, result_col = st.columns(2)
    mode_options = list(mode_labels)
    legacy_modes = {
        "caption": config.MODE_ONLY_MISSING, "missing": config.MODE_ONLY_MISSING,
        "update": config.MODE_UPDATE, "all": config.MODE_ALL,
        "skip_processed": config.MODE_SKIP_PROCESSED,
    }
    current_mode = legacy_modes.get(ss.get("mode"), ss.get("mode", mode_options[0]))
    if current_mode not in mode_options:
        current_mode = mode_options[0]
    with intent_col:
        ss.mode = st.selectbox(
            "Какие файлы обработать",
            mode_options,
            index=mode_options.index(current_mode),
            format_func=mode_labels.get,
            help="Безопасный вариант по умолчанию — продолжить незавершённую обработку.",
            disabled=not view.can_change_dataset,
        )
    pipeline_options = list(pipeline_labels)
    current_pipeline = ss.get("pipeline_mode", pipeline_options[0])
    if current_pipeline not in pipeline_options:
        current_pipeline = pipeline_options[0]
    with result_col:
        ss.pipeline_mode = st.selectbox(
            "Что сохранить в подписи",
            pipeline_options,
            index=pipeline_options.index(current_pipeline),
            format_func=pipeline_labels.get,
            help="Обычное описание подходит для большинства caption-датасетов.",
            disabled=not view.can_change_dataset,
        )
    if ss.pipeline_mode != "description_only":
        from core.taggers.registry import TAGGER_SPECS
        installed = [key for key in TAGGER_SPECS if ss.tagger_manager.installed(key)]
        ss.pipeline_tagger_ids = st.multiselect(
            "Модели тегов",
            installed,
            default=[key for key in ss.get("pipeline_tagger_ids", []) if key in installed],
            help="Установка дополнительных моделей находится в разделе «Ресурсы».",
        )
        if not installed:
            st.warning("Модели тегов не установлены. Выберите «Описание» или установите модель в «Ресурсах».")

    app_settings.save_settings_if_changed(ss)

    mode_banner(
        expert=bool(ss.get("show_advanced", False)),
        title="Ручной контроль включён" if ss.get("show_advanced", False) else "Автоматическая конфигурация активна",
        copy=("Параметры модели заданы вручную."
               if ss.get("show_advanced", False)
               else "Программа сама подбирает параметры модели и использование ресурсов."),
        settings={
            "Метод": mode_labels.get(ss.get("mode", config.MODE_RESUME), ss.get("mode", config.MODE_RESUME)),
            "Модель": os.path.basename(ss.get("llama_model") or ss.get("model", "auto")),
            "Результат": pipeline_labels.get(ss.get("pipeline_mode", "description_only"), "Описание"),
            "Статус": view.phase,
        },
    )
    st.markdown(f"### Следующий шаг: {view.title}")
    st.caption(view.hint)

    total = int(snapshot.get("total", 0) or 0)
    done = int(snapshot.get("done", 0) or 0)
    cols = st.columns(4)
    cols[0].metric("Статус", "Пауза" if snapshot.get("paused") else ("Работает" if snapshot.get("running") else "Готов"))
    cols[1].metric("Обработано", f"{done}/{total}" if total else "—")
    cols[2].metric("Ошибки", int(snapshot.get("errors", 0) or 0))
    cols[3].metric("Запуск", snapshot.get("run_id") or "—")
    if total:
        st.progress(view.progress, text=f"Прогресс {done}/{total}")
    if snapshot.get("status_msg"):
        st.caption(snapshot["status_msg"])

    scan = ss.get("scan_info")
    if scan:
        metrics = st.columns(4)
        metrics[0].metric("Изображения", scan.get("total", 0))
        metrics[1].metric("С подписями", scan.get("with_caption", 0))
        metrics[2].metric("Без подписей", scan.get("missing", 0))
        metrics[3].metric("Создано здесь", scan.get("done_by_app", 0))

    with st.expander("Готовность и выбранный способ", expanded=not bool(scan)):
        backend_type = ss.get("backend_type", "external")
        if backend_type == "managed_llama":
            model_ready = bool(ss.get("llama_model") and os.path.isfile(ss.llama_model))
            st.write(f"{'✓' if model_ready else '○'} Модель llama.cpp")
        else:
            model_ready = bool(ss.get("api_url"))
            st.write(f"{'✓' if model_ready else '○'} Адрес сервера модели")
        st.write(f"{'✓' if view.dataset_ready else '○'} Папка с изображениями")
        st.write(f"{'✓' if scan else '○'} Папка проверена")
        pipeline_mode = ss.get("pipeline_mode", "description_only")
        st.caption(
            f"Результат: {pipeline_labels.get(pipeline_mode, pipeline_mode)}; модель: "
            f"{os.path.basename(ss.get('llama_model') or ss.get('model', 'auto'))}; "
            f"оптимизация: {ss.get('llama_optimization_mode', 'auto')}."
        )
        if ss.get("llama_optimization_mode", "auto") != "manual":
            st.caption("Параметры ресурсов выбираются автоматически.")

    if ss.get("backend_type") == "managed_llama" and not ss.get("show_advanced", False):
        with st.expander("Выбрать модель", expanded=True):
            refresh_col, refresh_hint = st.columns([1, 4], vertical_alignment="center")
            if refresh_col.button("Обновить список", key="refresh_auto_model_library"):
                clear_model_library_cache()
                st.rerun()
            refresh_hint.caption(
                "Список кэшируется: изменение промпта больше не сканирует GGUF-файлы заново."
            )
            model_dir = st.text_input(
                "Папка VLM-моделей", value=ss.get("model_directory", ""),
                key="auto_model_directory",
                help="Папка с основными GGUF-файлами модели.",
            )
            mmproj_dir = st.text_input(
                "Папка vision/mmproj", value=ss.get("mmproj_directory", ""),
                key="auto_mmproj_directory",
                help="Отдельная папка допустима: Auto ищет projector независимо от основной модели.",
            )
            ss.model_directory = model_dir
            ss.mmproj_directory = mmproj_dir
            library = scan_model_library(model_dir, mmproj_roots=(mmproj_dir,))
            models = [entry.path for entry in library.entries]
            if ss.get("llama_model") and ss.llama_model not in models and os.path.isfile(ss.llama_model):
                models.insert(0, ss.llama_model)
            if models:
                current = ss.get("llama_model") if ss.get("llama_model") in models else models[0]
                ss.llama_model = st.selectbox(
                    "Основная VLM-модель", models, index=models.index(current),
                    format_func=os.path.basename, key="auto_llama_model",
                )
                selected = next((entry for entry in library.entries if entry.path == ss.llama_model), None)
                if selected:
                    meta = selected.metadata
                    st.caption(f"Найдена: {meta.architecture or 'GGUF'} · {meta.quantization or 'quantization ?'}")
            else:
                ss.llama_model = ""
                st.warning("В папке VLM-моделей GGUF не найдено.")

            mmproj_files = discover_gguf(mmproj_dir)
            options = [""] + mmproj_files
            current_mmproj = ss.get("llama_mmproj") if ss.get("llama_mmproj") in options else (mmproj_files[0] if len(mmproj_files) == 1 else "")
            ss.llama_mmproj = st.selectbox(
                "Vision projector", options, index=options.index(current_mmproj),
                format_func=lambda path: os.path.basename(path) if path else "Авто: без отдельного projector",
                key="auto_llama_mmproj",
            )
            if mmproj_files and not ss.llama_mmproj:
                st.info("Найден vision projector, но он пока не выбран.")

    if scan and folder and not snapshot.get("running"):
        st.markdown("### Проверка перед запуском")
        images = find_images(folder, bool(ss.get("recursive", False)))
        if images:
            chosen = st.selectbox("Изображение для проверки", images,
                                  format_func=os.path.basename, key="preview_image")
            preview = ss.preview_runner.snapshot()
            if st.button("Проверить результат", disabled=preview["running"]):
                if ss.preview_runner.start(chosen, get_params(), get_client(), ss.tagger_manager):
                    st.rerun()
            if preview["running"]:
                st.info("Идёт проверка. Файлы в выбранной папке не изменяются.")
            elif preview["error"]:
                st.error(preview["error"])
            elif preview["result"] is not None:
                result = preview["result"]
                if result.success:
                    st.success("Проверка завершена. Файлы не изменены.")
                    st.text_area("Предлагаемая подпись", result.final_caption,
                                 height=180, disabled=True, key="preview_result_caption")
                    if result.warnings:
                        st.warning("\n".join(result.warnings))
                else:
                    st.error(result.error or "Проверка завершилась без результата")
