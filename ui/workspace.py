"""Пользовательская рабочая область: контекст dataset/run и следующий шаг.

Это тонкий UX-слой над существующими вкладками. Он не дублирует операции и не
трогает core, а даёт пользователю единую точку ориентации.
"""

from __future__ import annotations

import os

import streamlit as st

from core import app_settings
from core.image_scanner import find_images, scan_summary
from core.inference.installer import discover_gguf
from core.models.library import scan_model_library
from ui.common import browse_into
from ui.context import get_registry
from ui.context import get_client, get_params
from ui.viewmodels import sync_dataset_context, workspace_view
from ui.design import mode_banner, section_heading

def render_workspace() -> None:
    ss = st.session_state
    worker = ss.worker
    snapshot = worker.snapshot()
    folder = ss.get("folder", "")
    view = workspace_view(folder, ss.get("scan_info"), snapshot)
    mode_labels = {
        "caption": "Продолжить обработку",
        "missing": "Заполнить пропуски",
        "update": "Обновить подписи",
        "all": "Пересоздать всё",
        "skip_processed": "Учитывать дату файлов",
    }
    pipeline_labels = {
        "description_only": "Описание",
        "tags_only": "Только теги",
        "tags_and_description": "Теги и описание",
        "tags_to_vlm_context": "Теги помогают описанию",
    }

    section_heading("РАБОТА", "Подготовьте запуск")
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

    mode_banner(
        expert=bool(ss.get("show_advanced", False)),
        title="Ручной контроль включён" if ss.get("show_advanced", False) else "Автоматическая конфигурация активна",
        copy=("Параметры модели заданы вручную."
               if ss.get("show_advanced", False)
               else "Программа сама подбирает параметры модели и использование ресурсов."),
        settings={
            "Метод": mode_labels.get(ss.get("mode", "caption"), ss.get("mode", "caption")),
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
