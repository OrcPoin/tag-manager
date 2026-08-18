"""Run Center: активный запуск, история и воспроизводимые параметры."""

from __future__ import annotations

import os

import streamlit as st

from core.run import list_run_snapshots
from ui.design import empty_state, section_heading


def render_runs() -> None:
    ss = st.session_state
    snapshot = ss.worker.snapshot()
    section_heading("ЗАПУСКИ", "История обработки",
                    "Здесь хранятся результаты и фактические параметры прошлых запусков.")

    if snapshot.get("running"):
        st.info(f"Сейчас выполняется: {snapshot.get('status_msg') or 'обработка'}")
        active = st.columns(3)
        active[0].metric("Готово", f"{snapshot.get('done', 0)}/{snapshot.get('total', 0)}")
        active[1].metric("Ошибки", snapshot.get("errors", 0))
        active[2].metric("Запуск", snapshot.get("run_id") or "—")

    folder = ss.get("folder", "")
    if not folder or not os.path.isdir(folder):
        empty_state("Сначала выберите папку", "После обработки здесь появится история запусков.")
        return
    runs = list_run_snapshots(folder)
    if not runs:
        empty_state("Запусков пока нет", "Начните обработку, чтобы сохранить её параметры и результат.")
        return

    latest, older = runs[0], runs[1:]
    latest_summary = latest.get("summary") or {}
    st.markdown("### Последний запуск")
    latest_metrics = st.columns(4)
    latest_metrics[0].metric("Статус", latest.get("status", "—"))
    latest_metrics[1].metric("Готово", latest_summary.get("update_done") or latest_summary.get("done", 0))
    latest_metrics[2].metric("Ошибки", latest_summary.get("update_errors") or latest_summary.get("errors", 0))
    latest_metrics[3].metric("Модель", latest.get("model") or "—")

    if not older:
        return
    st.markdown("### Предыдущие запуски")
    for run in older:
        status = run.get("status", "unknown")
        label = f"{run.get('started_at', '')[:19]} · {status} · {run.get('model') or 'модель не указана'}"
        with st.expander(label):
            info = st.columns(3)
            info[0].write(f"**Номер запуска:** `{run.get('run_id', '')}`")
            info[1].write(f"**Способ запуска модели:** {run.get('backend', '—')}")
            info[2].write(f"**Завершён:** {run.get('finished_at') or 'нет'}")
            params = run.get("params") or {}
            summary = run.get("summary") or {}
            if summary:
                totals = st.columns(4)
                totals[0].metric("Всего", summary.get("update_total") or summary.get("total", 0))
                totals[1].metric("Готово", summary.get("update_done") or summary.get("done", 0))
                totals[2].metric("Пропущено", summary.get("update_skipped") or summary.get("skipped", 0))
                totals[3].metric("Ошибки", summary.get("update_errors") or summary.get("errors", 0))
            relevant = {key: params[key] for key in (
                "preset_name", "pipeline_mode", "temperature", "max_tokens",
                "top_p", "manual_review", "trigger_word",
            ) if key in params}
            if relevant:
                st.json(relevant, expanded=False)
