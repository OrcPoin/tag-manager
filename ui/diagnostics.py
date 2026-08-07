"""Отдельный экран диагностики, не участвующий в основном workflow."""

from __future__ import annotations

import os

import streamlit as st

import config
from core.diagnostics import export_diagnostics
from core.hardware.detector import detect_hardware
from ui.design import section_heading


def render_diagnostics() -> None:
    ss = st.session_state
    snapshot = ss.worker.snapshot()
    section_heading("СИСТЕМА", "Диагностика")

    hardware = detect_hardware()
    metrics = st.columns(4)
    metrics[0].metric("Запуск модели", ss.get("backend_type", "—"))
    metrics[1].metric("Модель", os.path.basename(ss.get("llama_model") or ss.get("model", "—")))
    metrics[2].metric("CPU", f"{hardware.physical_cores}/{hardware.logical_cores} ядер")
    metrics[3].metric("Запуск", snapshot.get("run_id") or "—")

    with st.expander("Фактические настройки", expanded=True):
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
