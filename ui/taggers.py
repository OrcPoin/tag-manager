"""Opt-in tagger installation and preview UI."""

from __future__ import annotations

import threading
import time
import os

import streamlit as st

from core.taggers.installer import DownloadCancelled
from core.taggers.registry import TAGGER_SPECS
from core.taggers.manager import TaggerManager
from core.taggers.normalize import TagFilterPolicy, normalize_tagger_result
from core.pipeline import PipelineMode, PipelineOrchestrator, write_pipeline_sidecar
from core.taggers.gpu_runtime import TaggerGpuRuntime


def _manager() -> TaggerManager:
    return st.session_state.tagger_manager


def _download_state() -> dict:
    return st.session_state.setdefault("tagger_download", {
        "thread": None, "cancel": None, "done": 0, "total": 0,
        "asset": "", "error": "", "tagger_id": "",
    })


def _cancel_confirmation(tagger_id: str) -> None:
    st.session_state[f"confirm_{tagger_id}"] = False


def render_taggers_tab() -> None:
    manager = _manager()
    state = _download_state()
    st.subheader("Модели распознавания тегов")
    st.caption(
        "Эти модели нужны только для создания тегов. Они устанавливаются отдельно после подтверждения."
    )

    runtime = TaggerGpuRuntime(os.path.join(manager.installer.root, "runtime"))
    with st.expander("Ускорение на видеокарте", expanded=not runtime.installed()):
        st.write(runtime.status())
        st.caption("Изолированная установка официальных NVIDIA cuDNN/cuBLAS wheels; системная CUDA не изменяется.")
        if not runtime.installed():
            if st.button("Установить поддержку NVIDIA", type="primary"):
                with st.spinner("Скачивание cuDNN/cuBLAS (~1 ГБ)…"):
                    try:
                        runtime.install(); runtime.activate()
                        st.success("Поддержка NVIDIA установлена. Можно проверить модель.")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
        else:
            if st.button("Проверить ускорение NVIDIA"):
                try:
                    import onnxruntime as ort
                    runtime.activate()
                    st.write({"available_providers": ort.get_available_providers(), "dll_dirs": runtime.dll_dirs})
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
            if st.button("Удалить поддержку NVIDIA"):
                runtime.remove(); st.rerun()

    installed_ids = [tagger_id for tagger_id in TAGGER_SPECS if manager.installed(tagger_id)]
    with st.expander("Проверить совместную обработку", expanded=False):
        selected = st.multiselect("Модели тегов", installed_ids, default=installed_ids[:1])
        pipeline_mode = st.selectbox(
            "Состав результата",
            [mode.value for mode in PipelineMode],
            index=3,
        )
        pipeline_image = st.text_input("Тестовое изображение", key="pipeline_test_image")
        pipeline_prompt = st.text_area("Задание для модели", "Describe the image accurately.", key="pipeline_prompt")
        if st.button("Проверить обработку", disabled=not pipeline_image):
            try:
                from ui.context import get_client
                taggers = [manager.create(tagger_id) for tagger_id in selected]
                mode = PipelineMode(pipeline_mode)
                backend = None if mode == PipelineMode.TAGS_ONLY else get_client()
                result = PipelineOrchestrator(backend, taggers).run(
                    pipeline_image, mode=mode, user_prompt=pipeline_prompt,
                    system_prompt="You are an accurate image captioning assistant.",
                )
                sidecar = write_pipeline_sidecar(pipeline_image, result)
                if result.success:
                    st.success("Проверка завершена")
                    st.text_area("Результат", result.final_caption, height=180)
                else:
                    st.error(result.error)
                if result.warnings:
                    st.warning("\n".join(result.warnings))
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    active_thread = state.get("thread")
    if active_thread and active_thread.is_alive():
        fraction = state["done"] / max(1, state["total"])
        st.progress(min(1.0, fraction), f"Скачивание {state['asset']}…")
        if st.button("Отменить загрузку", width="stretch"):
            state["cancel"].set()
            st.info("Отмена запрошена; текущий блок загрузки завершится безопасно.")
        time.sleep(0.25)
        st.rerun()
        return

    if state.get("error"):
        st.error(state["error"])
        if st.button("Очистить ошибку"):
            state["error"] = ""
            st.rerun()

    for tagger_id, spec in TAGGER_SPECS.items():
        installed = manager.installed(tagger_id)
        with st.expander(spec.display_name, expanded=not installed):
            st.write(f"Источник: `{spec.repo_id}` · revision `{spec.revision}`")
            st.write(f"Лицензия: `{spec.license}`")
            st.write(f"Размер загрузки: ~{spec.download_size_bytes / (1 << 20):.0f} МБ")
            st.caption(spec.notes)
            if installed:
                st.success("Модель установлена локально")
                if st.button("Проверить загрузку", key=f"health_{tagger_id}"):
                    try:
                        tagger = manager.create(tagger_id)
                        tagger.load()
                        ok, message = tagger.health_check()
                        (st.success if ok else st.error)(message)
                        warning = tagger.metadata().get("provider_warning")
                        if warning:
                            st.warning(str(warning))
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
                preview_path = st.text_input(
                    "Изображение для preview", key=f"preview_path_{tagger_id}",
                    value="",
                )
                thresholds = st.columns(3)
                general_threshold = thresholds[0].number_input(
                    "General", 0.0, 1.0, 0.35, 0.05, key=f"general_thr_{tagger_id}"
                )
                character_threshold = thresholds[1].number_input(
                    "Character", 0.0, 1.0, 0.75, 0.05, key=f"char_thr_{tagger_id}"
                )
                top_k = thresholds[2].number_input(
                    "Top K", 1, 1000, 128, key=f"topk_{tagger_id}"
                )
                with st.expander("Фильтрация и нормализация"):
                    include_characters = st.checkbox(
                        "Character tags", True, key=f"include_char_{tagger_id}"
                    )
                    include_rating = st.checkbox(
                        "Rating tags", False, key=f"include_rating_{tagger_id}"
                    )
                    blacklist_text = st.text_area(
                        "Blacklist (по одному тегу на строку)",
                        key=f"blacklist_{tagger_id}", height=90,
                    )
                    whitelist_text = st.text_area(
                        "Whitelist (пусто = все)",
                        key=f"whitelist_{tagger_id}", height=90,
                    )
                    required_text = st.text_area(
                        "Required tags", key=f"required_{tagger_id}", height=70,
                    )
                    aliases_text = st.text_area(
                        "Aliases (`старый=новый`)", key=f"aliases_{tagger_id}", height=90,
                    )
                if st.button("Запустить preview", key=f"preview_{tagger_id}",
                             disabled=not preview_path):
                    try:
                        tagger = manager.create(tagger_id)
                        raw = tagger.predict(
                            preview_path,
                            general_threshold=0.0,
                            character_threshold=0.0,
                            top_k=int(top_k),
                        )
                        aliases = {}
                        for line in aliases_text.splitlines():
                            if "=" in line:
                                old, new = line.split("=", 1)
                                if old.strip() and new.strip():
                                    aliases[old.strip()] = new.strip()
                        result = normalize_tagger_result(raw, TagFilterPolicy(
                            general_threshold=float(general_threshold),
                            character_threshold=float(character_threshold),
                            blacklist=frozenset(
                                line.strip() for line in blacklist_text.splitlines() if line.strip()
                            ),
                            whitelist=frozenset(
                                line.strip() for line in whitelist_text.splitlines() if line.strip()
                            ),
                            aliases=aliases,
                            required_tags=tuple(
                                line.strip() for line in required_text.splitlines() if line.strip()
                            ),
                            include_characters=include_characters,
                            include_rating=include_rating,
                            top_k=int(top_k),
                        ))
                        if not result.success:
                            st.error(result.error)
                        else:
                            warning = result.metadata.get("provider_warning")
                            if warning:
                                st.warning(str(warning))
                            rows = [
                                {"tag": tag.name, "confidence": round(tag.confidence, 4),
                                 "category": tag.category}
                                for tag in result.tags
                            ]
                            st.dataframe(rows, width="stretch", height=360)
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
                if st.button("Удалить локальную модель", key=f"remove_{tagger_id}"):
                    manager.unload(tagger_id)
                    manager.installer.remove(spec)
                    st.rerun()
                continue

            st.warning("Модель не скачана")
            confirm = st.checkbox(
                "Я подтверждаю загрузку этой модели",
                key=f"confirm_{tagger_id}",
            )
            download_col, cancel_col = st.columns(2)
            with download_col:
                if st.button(
                    "Скачать модель", key=f"download_{tagger_id}",
                    type="primary", disabled=not confirm, width="stretch",
                ):
                    state.update({
                        "done": 0, "total": 0, "asset": "подготовка",
                        "error": "", "tagger_id": tagger_id,
                    })
                    cancel_event = threading.Event()
                    state["cancel"] = cancel_event

                    def progress(done, total, asset):
                        state["done"] = done
                        state["total"] = total
                        state["asset"] = asset

                    def run_install(selected_spec=spec, selected_cancel=cancel_event):
                        try:
                            manager.installer.install(
                                selected_spec, progress=progress,
                                cancelled=selected_cancel.is_set,
                            )
                        except DownloadCancelled:
                            state["error"] = "Загрузка отменена пользователем; partial-файлы сохранены."
                        except Exception as exc:  # noqa: BLE001
                            state["error"] = str(exc)
                        finally:
                            state["thread"] = None

                    state["thread"] = threading.Thread(target=run_install, daemon=True)
                    state["thread"].start()
                    st.rerun()
            with cancel_col:
                st.button(
                    "Отмена", key=f"cancel_{tagger_id}", width="stretch",
                    on_click=_cancel_confirmation, args=(tagger_id,),
                )
