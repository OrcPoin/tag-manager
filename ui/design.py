"""Единый визуальный слой Streamlit-интерфейса."""

from __future__ import annotations

from html import escape

import streamlit as st


def inject_design_system() -> None:
    dark = st.session_state.get("ui_theme", "light") == "dark"
    theme_css = """
    :root { --tm-bg: #151b1e; --tm-surface: #20292d; --tm-surface-soft: #173f39;
      --tm-text: #edf3f4; --tm-muted: #b2c0c4; --tm-border: #3b4a4f;
      --tm-auto-soft: #164b42; --tm-expert-soft: #503817; }
    html { color-scheme: dark; }
    .stApp { background: var(--tm-bg); }
    [data-testid="stMain"] p, [data-testid="stMain"] label, [data-testid="stMain"] span { color: var(--tm-text); }
    [data-testid="stMain"] input, [data-testid="stMain"] textarea,
    [data-testid="stMain"] div[data-baseweb="select"] > div { background: var(--tm-surface); color: var(--tm-text); border-color: var(--tm-border); }
    .tm-product-name, .tm-section-title, .tm-mode-title, .tm-effective-value { color: var(--tm-text); }
    .tm-mode-copy { color: #c2d0d2; }
    div[data-testid="stMetric"], div[data-testid="stExpander"], .tm-effective-item { background: var(--tm-surface); }
    div[data-testid="stSegmentedControl"] > div { background: #273338; }
    [data-testid="stSidebar"] { background: #202a2e; border-right-color: #334147; }
    [data-testid="stSidebar"] * { color: #eef3f4; }
    [data-testid="stSidebar"] div[data-baseweb="select"] > div,
    [data-testid="stSidebar"] div[data-baseweb="input"] > div { background: #2b373b; border-color: #4a5b60; }
    """ if dark else """
    html { color-scheme: light; }
    [data-testid="stMain"] p, [data-testid="stMain"] label { color: var(--tm-text); }
    [data-testid="stSidebar"] { background: #ffffff; border-right-color: var(--tm-border); }
    [data-testid="stSidebar"] * { color: var(--tm-text); }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: var(--tm-muted); }
    [data-testid="stSidebar"] div[data-baseweb="select"] > div,
    [data-testid="stSidebar"] div[data-baseweb="input"] > div { background: #ffffff; border-color: var(--tm-border); }
    [data-testid="stExpander"], [data-testid="stExpander"] details,
    [data-testid="stExpander"] summary, [data-testid="stExpanderDetails"] { background: #ffffff !important; color: #172126 !important; }
    [data-testid="stExpander"] summary[aria-expanded="true"] { background: #f4f7f8 !important; }
    [data-testid="stExpander"] p, [data-testid="stExpander"] span,
    [data-testid="stExpander"] label { color: #172126 !important; }
    [data-testid="stAlert"] *, [data-testid="stMetric"] *,
    [data-testid="stRadio"] *, [data-testid="stCheckbox"] * { color: #172126 !important; }
    [data-testid="stTabs"] button, [data-testid="stTabs"] button * { color: #526168 !important; }
    [data-testid="stTabs"] button[aria-selected="true"],
    [data-testid="stTabs"] button[aria-selected="true"] * { color: #087f6d !important; }
    [data-baseweb="tag"] { background: #dff3ee !important; }
    [data-baseweb="tag"] * { color: #17483f !important; }
    """
    control_css = """
    [data-testid="stMain"] .stButton > button, [data-testid="stMain"] .stDownloadButton > button { background: #273338 !important; color: #eef3f4 !important; border-color: #4a5b60 !important; }
    [data-testid="stMain"] .stButton > button *, [data-testid="stMain"] .stDownloadButton > button * { color: #eef3f4 !important; }
    [data-testid="stMain"] .stButton > button[kind="primary"] { background: #087f6d !important; border-color: #087f6d !important; }
    [data-testid="stMain"] .stButton > button[kind="primary"] * { color: #ffffff !important; }
    """ if dark else """
    [data-testid="stMain"] .stButton > button, [data-testid="stMain"] .stDownloadButton > button { background: #ffffff !important; color: #172126 !important; border-color: #bfcace !important; }
    [data-testid="stMain"] .stButton > button *, [data-testid="stMain"] .stDownloadButton > button * { color: #172126 !important; }
    [data-testid="stMain"] .stButton > button[kind="primary"] { background: #087f6d !important; border-color: #087f6d !important; }
    [data-testid="stMain"] .stButton > button[kind="primary"] * { color: #ffffff !important; }
    [data-testid="stMain"] .stButton > button:disabled { background: #edf1f2 !important; color: #718087 !important; border-color: #d9e0e3 !important; }
    [data-testid="stMain"] .stButton > button:disabled * { color: #718087 !important; }
    """
    st.markdown(
        """
<style>
:root {
  --tm-bg: #f3f5f7;
  --tm-surface: #ffffff;
  --tm-surface-soft: #eef7f5;
  --tm-text: #172126;
  --tm-muted: #647178;
  --tm-border: #d9e0e3;
  --tm-auto: #087f6d;
  --tm-auto-soft: #dff3ee;
  --tm-expert: #a65b00;
  --tm-expert-soft: #fff0d8;
  --tm-danger: #b42318;
  --tm-radius: 7px;
}

.stApp {
  background: var(--tm-bg);
  color: var(--tm-text);
  font-family: "Segoe UI Variable", "Segoe UI", Arial, sans-serif;
}

[data-testid="stDecoration"], [data-testid="stToolbarActions"],
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"], #MainMenu, footer { display: none !important; }
[data-testid="stToolbar"] { display: flex !important; background: transparent !important; }
[data-testid="stSidebarCollapsedControl"], [data-testid="stSidebarCollapseButton"] {
  display: flex !important; visibility: visible !important; opacity: 1 !important;
}
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebarCollapseButton"] button { border-radius: 6px !important; }
[data-testid="stHeader"] { display: block !important; height: 2.5rem; background: transparent !important; }
[data-testid="stAppViewContainer"] > .main { padding-top: 0 !important; }

[data-testid="stMainBlockContainer"] {
  max-width: 1480px;
  padding-top: 1.4rem;
  padding-bottom: 3rem;
}

h1, h2, h3, h4, p, label, button, input, textarea { letter-spacing: 0 !important; }
h1 { font-size: 1.8rem !important; line-height: 1.2 !important; font-weight: 720 !important; }
h2 { font-size: 1.25rem !important; line-height: 1.3 !important; font-weight: 700 !important; }
h3 { font-size: 1.02rem !important; line-height: 1.35 !important; font-weight: 680 !important; }

[data-testid="stSidebar"] {
  background: #202a2e;
  border-right: 1px solid #334147;
}
[data-testid="stSidebar"] * { color: #eef3f4; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #b8c5c9; }

.tm-product-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 4px 0 16px;
  margin-bottom: 4px;
}
.tm-top-divider { height: 1px; background: var(--tm-border); margin: 4px 0 14px; }
.tm-product-name { font-size: 1.55rem; font-weight: 760; color: var(--tm-text); }
.tm-product-subtitle { margin-top: 3px; color: var(--tm-muted); font-size: .91rem; }
.tm-live-mark { color: var(--tm-auto); font-weight: 650; font-size: .86rem; white-space: nowrap; }

.tm-section-heading { margin: 20px 0 10px; }
.tm-section-kicker { color: var(--tm-auto); font-size: .72rem; font-weight: 760; text-transform: uppercase; }
.tm-section-title { color: var(--tm-text); font-size: 1.18rem; font-weight: 720; margin-top: 3px; }
.tm-section-copy { color: var(--tm-muted); font-size: .88rem; margin-top: 3px; }

.tm-mode-banner {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 18px;
  padding: 13px 15px; border: 1px solid #a9d8cd; border-left: 4px solid var(--tm-auto);
  background: var(--tm-auto-soft); border-radius: var(--tm-radius); margin: 8px 0 14px;
}
.tm-mode-banner.expert { background: var(--tm-expert-soft); border-color: #e4c28b; border-left-color: var(--tm-expert); }
.tm-mode-title { font-weight: 740; color: var(--tm-text); }
.tm-mode-copy { color: #4e6267; font-size: .85rem; margin-top: 2px; }
.tm-mode-badge { font-size: .7rem; font-weight: 800; text-transform: uppercase; white-space: nowrap; padding: 4px 7px; border-radius: 4px; background: var(--tm-auto); color: white; }
.expert .tm-mode-badge { background: var(--tm-expert); }

.tm-effective-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 8px 0 4px; }
.tm-effective-item { padding: 9px 10px; background: rgba(255,255,255,.76); border: 1px solid #c9dfda; border-radius: 6px; min-width: 0; }
.tm-effective-label { color: var(--tm-muted); font-size: .68rem; font-weight: 720; text-transform: uppercase; }
.tm-effective-value { color: var(--tm-text); font-size: .86rem; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.tm-mode-card { min-height: 108px; padding: 13px 13px 10px; background: var(--tm-surface); border: 1px solid var(--tm-border); border-radius: var(--tm-radius); margin-bottom: 7px; }
.tm-mode-card.selected { border: 2px solid var(--tm-auto); background: var(--tm-auto-soft); padding: 12px 12px 9px; }
.tm-mode-card-title { color: var(--tm-text); font-size: .91rem; font-weight: 740; line-height: 1.25; }
.tm-mode-card-copy { color: var(--tm-muted); font-size: .76rem; line-height: 1.35; margin-top: 6px; }
.tm-mode-card-mark { color: var(--tm-auto); font-size: .68rem; font-weight: 780; text-transform: uppercase; margin-bottom: 5px; }

.tm-nav-card { min-height: 76px; padding: 10px 11px; background: var(--tm-surface); border: 1px solid var(--tm-border); border-radius: var(--tm-radius); }
.tm-nav-card.active { border: 2px solid var(--tm-auto); background: var(--tm-auto-soft); padding: 9px 10px; }
.tm-nav-title { color: var(--tm-text); font-size: .88rem; font-weight: 750; }
.tm-nav-copy { color: var(--tm-muted); font-size: .7rem; line-height: 1.3; margin-top: 4px; }

.tm-primary-nav .stButton > button { min-height: 62px; justify-content: flex-start; text-align: left; padding: 10px 13px; }
.tm-primary-nav .stButton > button p { font-size: .86rem; line-height: 1.25; }
.tm-mode-picker .stButton > button { min-height: 72px; align-items: flex-start; justify-content: flex-start; text-align: left; padding: 10px 12px; }

.tm-empty { padding: 28px 24px; text-align: center; border: 1px dashed #aebdc2; background: var(--tm-surface); border-radius: var(--tm-radius); margin: 12px 0; }
.tm-empty-title { color: var(--tm-text); font-size: 1rem; font-weight: 730; }
.tm-empty-copy { color: var(--tm-muted); font-size: .84rem; margin-top: 5px; }

div[data-testid="stSegmentedControl"] > div { background: #e7ecee; padding: 4px; border: 1px solid var(--tm-border); border-radius: 7px; }
div[data-testid="stSegmentedControl"] button { min-height: 38px; border-radius: 5px !important; font-weight: 680 !important; }
div[data-testid="stSegmentedControl"] button[aria-pressed="true"] { background: var(--tm-surface) !important; color: var(--tm-auto) !important; box-shadow: 0 1px 3px rgba(24,42,48,.12); }
div[data-testid="stSegmentedControl"] button[aria-pressed="true"] * { color: var(--tm-auto) !important; }
div[data-testid="stSegmentedControl"] button[aria-pressed="false"] * { color: var(--tm-text) !important; }

div[data-testid="stMetric"] { background: var(--tm-surface); border: 1px solid var(--tm-border); border-radius: var(--tm-radius); padding: 11px 13px; min-height: 82px; }
div[data-testid="stMetricLabel"] { color: var(--tm-muted); font-weight: 650; }
div[data-testid="stMetricValue"] { color: var(--tm-text); font-weight: 740; }

div[data-testid="stExpander"] { background: var(--tm-surface); border: 1px solid var(--tm-border); border-radius: var(--tm-radius); overflow: hidden; }
div[data-testid="stExpander"] summary { font-weight: 680; }

.stButton > button, .stDownloadButton > button { border-radius: 6px !important; min-height: 38px; font-weight: 680 !important; border-color: #bfcace !important; }
.stButton > button[kind="primary"] { background: var(--tm-auto) !important; border-color: var(--tm-auto) !important; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color: var(--tm-auto) !important; color: var(--tm-auto) !important; }
.st-key-theme_toggle .stButton > button { width: 40px !important; min-width: 40px !important; height: 40px !important; min-height: 40px !important; border-radius: 50% !important; padding: 0 !important; font-size: 1.05rem !important; box-shadow: 0 1px 3px rgba(20,35,40,.12); }
.st-key-theme_toggle { display: flex; justify-content: flex-end; align-items: center; }

div[data-baseweb="input"] > div, div[data-baseweb="select"] > div, textarea { border-radius: 6px !important; }
div[data-baseweb="select"] *, div[role="listbox"] *, div[role="option"] { color: var(--tm-text) !important; }
div[data-baseweb="select"] > div { background: var(--tm-surface) !important; }
div[role="option"][aria-selected="true"] { background: var(--tm-auto) !important; color: #ffffff !important; }
div[role="option"][aria-selected="true"] * { color: #ffffff !important; }
[data-baseweb="select"] input { color: var(--tm-text) !important; }
[data-testid="stAlert"] { border-radius: var(--tm-radius); border-width: 1px; }
[data-testid="stProgress"] > div > div { background: var(--tm-auto); }

@media (max-width: 760px) {
  [data-testid="stMainBlockContainer"] { padding-left: .8rem; padding-right: .8rem; }
  .tm-product-header, .tm-mode-banner { flex-direction: column; align-items: flex-start; gap: 8px; }
  .tm-effective-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  div[data-testid="stSegmentedControl"] button { min-height: 44px; font-size: .82rem; }
}
        """ + theme_css + control_css + "\n</style>",
        unsafe_allow_html=True,
    )


def product_header(status: str = "Готов к работе") -> None:
    st.markdown(
        f"""
<div class="tm-product-header">
  <div><div class="tm-product-name">Tag Manager</div>
  <div class="tm-product-subtitle">Подготовка подписей к изображениям для обучения</div></div>
  <div class="tm-live-mark">● {escape(status)}</div>
</div>
        """,
        unsafe_allow_html=True,
    )


def top_bar(status: str = "Готов к работе") -> str | None:
    """Верхняя строка продукта с компактным переключателем темы."""
    left, right = st.columns([12, 1], vertical_alignment="center")
    with left:
        product_header(status)
    with right:
        icon = "☀" if st.session_state.get("ui_theme") == "dark" else "☾"
        if st.button(icon, key="theme_toggle", help="Переключить светлую/тёмную тему"):
            return "light" if st.session_state.get("ui_theme") == "dark" else "dark"
    st.markdown('<div class="tm-top-divider"></div>', unsafe_allow_html=True)
    return None


def section_heading(kicker: str, title: str, copy: str = "") -> None:
    st.markdown(
        f"""<div class="tm-section-heading"><div class="tm-section-kicker">{escape(kicker)}</div>
<div class="tm-section-title">{escape(title)}</div>
<div class="tm-section-copy">{escape(copy)}</div></div>""",
        unsafe_allow_html=True,
    )


def empty_state(title: str, copy: str) -> None:
    st.markdown(
        f'<div class="tm-empty"><div class="tm-empty-title">{escape(title)}</div>'
        f'<div class="tm-empty-copy">{escape(copy)}</div></div>',
        unsafe_allow_html=True,
    )


def mode_banner(*, expert: bool, title: str, copy: str, settings: dict[str, object]) -> None:
    items = "".join(
        f'<div class="tm-effective-item"><div class="tm-effective-label">{escape(str(key))}</div>'
        f'<div class="tm-effective-value" title="{escape(str(value))}">{escape(str(value))}</div></div>'
        for key, value in settings.items()
    )
    st.markdown(
        f"""<div class="tm-mode-banner{' expert' if expert else ''}">
<div style="flex:1"><div class="tm-mode-title">{escape(title)}</div>
<div class="tm-mode-copy">{escape(copy)}</div><div class="tm-effective-grid">{items}</div></div>
<div class="tm-mode-badge">{'Expert' if expert else 'Auto'}</div></div>""",
        unsafe_allow_html=True,
    )
