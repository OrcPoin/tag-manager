"""Общий контекст для UI-модулей: доступ к разделяемым ресурсам и сборка
объектов из текущего session_state.

Почему отдельный модуль: и `app.py`, и все `ui/*`-вкладки нуждаются в
`get_client/get_params/get_registry` и в живых `worker`/`logger`. Держать их
здесь (а не в `app.py`) разрывает цикл `app ↔ ui`: вкладки импортируют context,
а не app. Всё читается из `st.session_state` в момент ВЫЗОВА, поэтому импорт на
уровне модуля безопасен (session_state ещё пуст на этапе импорта).
"""

from __future__ import annotations

import streamlit as st

import config
from core.caption_client import CaptionClient
from core.registry import DoneRegistry


def worker():
    """Живой singleton-воркер из session_state (создаётся в app.init_state)."""
    return st.session_state.worker


def logger():
    """Живой singleton-логгер из session_state."""
    return st.session_state.logger


def proc():
    """ProcessingState воркера (очередь/индекс/прогресс)."""
    return st.session_state.worker.state


def get_client() -> CaptionClient:
    ss = st.session_state
    return CaptionClient(
        base_url=ss.api_url,
        api_key=config.DEFAULT_API_KEY,
        model=ss.model,
        timeout=ss.timeout,
    )


def get_params() -> dict:
    """Снапшот параметров генерации для передачи в воркер."""
    ss = st.session_state
    params = {
        "system_prompt": ss.system_prompt,
        "user_prompt": ss.user_prompt,
        "temperature": ss.temperature,
        "max_tokens": ss.max_tokens,
        "top_p": ss.top_p,
        "auto_retry": ss.auto_retry,
        "manual_review": ss.manual_review,
        "disable_thinking": ss.disable_thinking,
        "trigger_word": ss.trigger_word,
    }
    if ss.get("mode") == config.MODE_UPDATE:
        params.update({
            "update_mechanism": ss.get("update_mechanism", config.DEFAULT_UPDATE_MECHANISM),
            "tag_strategy": ss.get("tag_strategy", config.DEFAULT_TAG_STRATEGY),
            "prose_strategy": ss.get("prose_strategy", config.DEFAULT_PROSE_STRATEGY),
            "manual_policy": ss.get("manual_policy", config.DEFAULT_MANUAL_POLICY),
        })
    return params


def get_registry() -> DoneRegistry:
    """Реестр «сделано этим приложением» для текущей папки (кэш в session_state)."""
    ss = st.session_state
    if ss.registry is None or ss.registry.folder != ss.folder:
        ss.registry = DoneRegistry(ss.folder)
    return ss.registry
