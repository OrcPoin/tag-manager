"""Общий контекст для UI-модулей: доступ к разделяемым ресурсам и сборка
объектов из текущего session_state.

Почему отдельный модуль: и `app.py`, и все `ui/*`-вкладки нуждаются в
`get_client/get_params/get_registry` и в живых `worker`/`logger`. Держать их
здесь (а не в `app.py`) разрывает цикл `app ↔ ui`: вкладки импортируют context,
а не app. Всё читается из `st.session_state` в момент ВЫЗОВА, поэтому импорт на
уровне модуля безопасен (session_state ещё пуст на этапе импорта).
"""

from __future__ import annotations

import os
import streamlit as st

import config
from core.inference.external_api import ExternalApiBackend
from core.inference.interfaces import InferenceBackend
from core.inference.llama_server import LlamaServerConfig
from core.inference.manager import BackendManager
from core.hardware.detector import detect_hardware
from core.hardware.optimizer import apply_manual_overrides, optimize_llama_profile
from core.compatibility import check_compatibility
from core.models.gguf import read_gguf_metadata
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


@st.cache_resource
def get_backend_manager() -> BackendManager:
    return BackendManager()


def get_client() -> InferenceBackend:
    ss = st.session_state
    manager = get_backend_manager()
    if ss.get("backend_type", config.DEFAULT_BACKEND_TYPE) == "managed_llama":
        extra_args = config.DEFAULT_LLAMA_EXTRA_ARGS
        if ss.llama_model and os.path.isfile(ss.llama_model):
            try:
                metadata = read_gguf_metadata(ss.llama_model)
                mmproj_size = (
                    os.path.getsize(ss.llama_mmproj)
                    if ss.llama_mmproj and os.path.isfile(ss.llama_mmproj) else 0
                )
                profile = optimize_llama_profile(
                    detect_hardware(), metadata,
                    reasoning=not bool(ss.disable_thinking),
                    mmproj_size_bytes=mmproj_size,
                    reasoning_budget=int(ss.llama_reasoning_budget),
                    mode=str(ss.llama_optimization_mode),
                )
                if ss.llama_optimization_mode == "manual":
                    profile = apply_manual_overrides(
                        profile,
                        context_size=int(ss.llama_context_size),
                        max_output_tokens=int(ss.max_tokens),
                        gpu_layers=str(ss.llama_gpu_layers),
                        fit_target=int(ss.llama_fit_target),
                        slots=int(ss.llama_slots),
                        threads=int(ss.llama_threads) or detect_hardware().physical_cores,
                        batch=int(ss.llama_batch),
                        ubatch=int(ss.llama_ubatch),
                        flash_attention=str(ss.llama_flash_attn),
                        load_mode=str(ss.llama_load_mode),
                        cache_k=str(ss.llama_cache_k),
                        cache_v=str(ss.llama_cache_v),
                        reasoning=not bool(ss.disable_thinking),
                        reasoning_budget=int(ss.llama_reasoning_budget),
                    )
                extra_args = profile.args
                ss.llama_optimization_profile = profile
                ss.llama_compatibility_report = check_compatibility(
                    detect_hardware(), metadata,
                    context_size=profile.context_size,
                    mmproj_size_bytes=mmproj_size,
                    slots=(int(ss.llama_slots) if profile.name == "manual" else 1),
                )
            except (OSError, ValueError):
                ss.llama_optimization_profile = None
                ss.llama_compatibility_report = None
        server_config = LlamaServerConfig(
            executable=ss.llama_executable,
            model=ss.llama_model,
            mmproj=ss.llama_mmproj,
            host=ss.llama_host,
            port=int(ss.llama_port),
            api_prefix=ss.llama_api_prefix,
            startup_timeout=float(ss.llama_startup_timeout),
            extra_args=extra_args,
            log_path=config.LLAMA_LOG_FILE,
        )
        return manager.managed(server_config, timeout=ss.timeout)
    return manager.external(
        base_url=ss.api_url, api_key=config.DEFAULT_API_KEY,
        model=ss.model, timeout=ss.timeout,
    )


def get_params() -> dict:
    """Снапшот параметров генерации для передачи в воркер."""
    ss = st.session_state
    profile = ss.get("llama_optimization_profile")
    max_tokens = int(ss.max_tokens)
    if ss.get("backend_type") == "managed_llama" and profile:
        max_tokens = min(max_tokens, profile.max_output_tokens)
    params = {
        "system_prompt": ss.system_prompt,
        "user_prompt": ss.user_prompt,
        "temperature": ss.temperature,
        "max_tokens": max_tokens,
        "top_p": ss.top_p,
        "auto_retry": ss.auto_retry,
        "manual_review": ss.manual_review,
        "disable_thinking": ss.disable_thinking,
        "trigger_word": ss.trigger_word,
        "pipeline_mode": ss.get("pipeline_mode", "description_only"),
        "pipeline_tagger_ids": ss.get("pipeline_tagger_ids", []),
        "pipeline_top_k": int(ss.get("pipeline_top_k", 128)),
        "tagger_root": "taggers",
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
