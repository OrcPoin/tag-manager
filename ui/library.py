"""Пользовательская библиотека профилей, пресетов и taggers."""

from __future__ import annotations

import streamlit as st
from core import presets as presets_mod
from ui.design import empty_state, section_heading


def render_library() -> None:
    ss = st.session_state
    section_heading("РЕСУРСЫ", "Профили и модели")
    if ss.get("library_section") in {"Профили", "Модели"}:
        del ss["library_section"]
    section = st.segmented_control(
        "Тип ресурса", ["Профили описания", "Модели тегов"], default="Профили описания",
        label_visibility="collapsed", key="library_section",
    )
    if section == "Модели тегов":
        from ui.taggers import render_taggers_tab
        render_taggers_tab()
        return

    st.markdown("### Профили описания")
    for name, preset in ss.presets.items():
        selected = name == ss.get("preset_name")
        with st.expander(f"{'✓ ' if selected else ''}{name}"):
            system_prompt = st.text_area(
                "System prompt", value=str(preset.get("system", "")), height=180,
                key=f"library_system_{name}",
            )
            user_prompt = st.text_area(
                "User prompt", value=str(preset.get("user", "")), height=280,
                key=f"library_user_{name}",
            )
            actions = st.columns(2)
            if actions[0].button("Использовать", key=f"use_profile_{name}",
                                 disabled=selected, width="stretch"):
                ss.preset_name = name
                ss.system_prompt = system_prompt
                ss.user_prompt = user_prompt
                st.rerun()
            if actions[1].button("Сохранить изменения", key=f"save_profile_{name}",
                                 type="primary", width="stretch"):
                presets_mod.save_preset(name, system_prompt, user_prompt)
                ss.presets = presets_mod.load_presets()
                if selected:
                    ss.system_prompt = system_prompt
                    ss.user_prompt = user_prompt
                st.toast(f"Профиль «{name}» сохранён")

    if not ss.presets:
        empty_state("Профилей пока нет", "Создайте профиль результата в разделе «Работа» и сохраните его для повторного использования.")

    if ss.get("backend_profiles"):
        st.markdown("### Профили запуска модели")
        for name in ss.backend_profiles:
            st.write(f"- {name}")
