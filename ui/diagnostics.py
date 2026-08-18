"""Отдельный экран диагностики, не участвующий в основном workflow."""

from __future__ import annotations

import os

import streamlit as st

import config
from core.diagnostics import export_diagnostics
from core.hardware.detector import detect_hardware, refresh_hardware
from ui.context import get_client, logger
from ui.design import empty_state, section_heading


def render_diagnostics() -> None:
    ss = st.session_state
    snapshot = ss.worker.snapshot()
    section_heading("СИСТЕМА", "Состояние приложения",
                    "Проверка окружения, фактических параметров и журналов.")

    section = st.segmented_control(
        "Раздел системы", ["Обзор", "Конфигурация", "Логи"],
        default="Обзор", label_visibility="collapsed", key="system_section",
    )

    hardware = detect_hardware()
    if section == "Обзор":
        metrics = st.columns(4)
        metrics[0].metric("Модель", os.path.basename(ss.get("llama_model") or ss.get("model", "—")))
        metrics[1].metric("CPU", f"{hardware.physical_cores}/{hardware.logical_cores} ядер")
        metrics[2].metric("RAM доступно", f"{hardware.ram_available_bytes / (1 << 30):.1f} ГБ")
        metrics[3].metric("GPU", hardware.primary_gpu.name if hardware.primary_gpu else "Не обнаружен")

        run_state = "Работает" if snapshot.get("running") else (
            "Завершён" if snapshot.get("finished") else "Нет активного запуска"
        )
        st.info(f"Состояние обработки: {run_state}. {snapshot.get('status_msg') or ''}")
        actions = st.columns(3)
        if actions[0].button("Проверить backend", type="primary", width="stretch"):
            ok, message = get_client().check_connection()
            (st.success if ok else st.error)(message)
        if actions[1].button("Обновить данные оборудования", width="stretch"):
            refresh_hardware()
            st.rerun()
        if actions[2].button("Сохранить диагностику", width="stretch"):
            path = export_diagnostics(
                config.DIAGNOSTICS_FILE,
                worker_snapshot=snapshot,
                profile=ss.get("llama_optimization_profile"),
                compatibility=ss.get("llama_compatibility_report"),
            )
            st.success(f"Отчёт сохранён: `{path}`")

        report = ss.get("llama_compatibility_report")
        if report:
            st.markdown("### Совместимость")
            st.write(report.summary)
            for recommendation in report.recommendations:
                st.write(f"- {recommendation}")
        return

    if section == "Логи":
        text = logger().get_text()
        if text:
            st.text_area("Журнал обработки", text, height=520, disabled=True,
                         key="system_log_view")
        else:
            empty_state("Журнал пока пуст", "Сообщения запуска и обработки появятся здесь.")
        return

    st.markdown("### Фактическая конфигурация")
    st.caption("Именно эти значения будут использованы при следующем запуске.")
    with st.expander("Параметры", expanded=True):
        st.json({
            "backend": ss.get("backend_type"),
            "model": ss.get("llama_model") or ss.get("model"),
            "optimization_mode": ss.get("llama_optimization_mode"),
            "context_size": ss.get("llama_context_size"),
            "max_tokens": ss.get("max_tokens"),
            "pipeline_mode": ss.get("pipeline_mode", "description_only"),
            "taggers": ss.get("pipeline_tagger_ids", []),
            "worker": snapshot,
        }, expanded=False)

    report = ss.get("llama_compatibility_report")
    if report:
        with st.expander("Совместимость и память"):
            st.write(report.summary)
            for recommendation in report.recommendations:
                st.write(f"- {recommendation}")

    if st.button("Сохранить отчёт диагностики"):
        path = export_diagnostics(
            config.DIAGNOSTICS_FILE,
            worker_snapshot=snapshot,
            profile=ss.get("llama_optimization_profile"),
            compatibility=report,
        )
        st.success(f"Отчёт сохранён: `{path}`")
