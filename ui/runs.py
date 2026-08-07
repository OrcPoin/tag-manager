"""Run Center: активный запуск, история и воспроизводимые параметры."""

from __future__ import annotations

import os

import streamlit as st

from core.run import list_run_snapshots
from ui.design import empty_state, section_heading


def render_runs() -> None:
    ss = st.session_state
    snapshot = ss.worker.snapshot()
    section_heading("ЗАПУСКИ", "История обработки")

    active = st.columns(4)
    active[0].metric("Состояние", snapshot.get("status_msg") or "Готов")
    active[1].metric("Готово", f"{snapshot.get('done', 0)}/{snapshot.get('total', 0)}")
    active[2].metric("Ошибки", snapshot.get("errors", 0))
    active[3].metric("Номер запуска", snapshot.get("run_id") or "—")

    folder = ss.get("folder", "")
    if not folder or not os.path.isdir(folder):
        empty_state("Сначала выберите папку", "После обработки здесь появится история запусков.")
        return
    runs = list_run_snapshots(folder)
    if not runs:
        empty_state("Запусков пока нет", "Начните обработку, чтобы сохранить её параметры и результат.")
        return

    st.markdown("### История")
    for run in runs:
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
