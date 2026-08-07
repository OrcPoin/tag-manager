"""Persistent named managed-backend profiles."""

from __future__ import annotations

import json
import os
import tempfile

import config


PROFILE_KEYS = (
    "llama_model", "llama_mmproj", "disable_thinking", "llama_reasoning_budget",
    "max_tokens", "llama_optimization_mode", "llama_context_size",
    "llama_gpu_layers", "llama_fit_target", "llama_slots", "llama_threads",
    "llama_batch", "llama_ubatch", "llama_flash_attn", "llama_load_mode",
    "llama_cache_k", "llama_cache_v",
)


def load_backend_profiles(path: str = config.BACKEND_PROFILES_FILE) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            return {}
        return {
            str(name): {key: values[key] for key in PROFILE_KEYS if key in values}
            for name, values in data.items()
            if isinstance(name, str) and name.strip() and isinstance(values, dict)
        }
    except (OSError, ValueError):
        return {}


def save_backend_profiles(
    profiles: dict[str, dict], path: str = config.BACKEND_PROFILES_FILE
) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".backend-profiles-", dir=folder, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(profiles, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def capture_profile(values) -> dict:
    return {key: values[key] for key in PROFILE_KEYS if key in values}
