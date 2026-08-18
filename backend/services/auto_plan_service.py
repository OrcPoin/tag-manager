from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4
import os
import shutil
import config
from core.app_settings import load_settings

from .project_service import ProjectService


@dataclass(slots=True)
class AutoPlanRequest:
    result_type: str = "hybrid_caption"
    scope: str = "missing"
    focus: str = "balanced"
    detail: str = "balanced"
    language: str = "ru"
    trigger_word: str = ""
    review_policy: str = "queue"
    analysis_mode: str = "fast"
    reasoning_budget: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    context_size: int | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    keep_alive_seconds: int | None = None
    auto_retry: bool | None = None
    pipeline_tagger_ids: list[str] | None = None
    pipeline_mode: str = "vlm"
    gpu_layers: int | None = None
    threads: int | None = None
    batch_size: int | None = None
    ubatch_size: int | None = None
    cache_type_k: str = "f16"
    cache_type_v: str = "f16"
    flash_attention: bool = True
    mmap: bool = True
    slots: int = 1
    additional_args: list[str] | None = None
    tagger_general_threshold: float = 0.35
    tagger_character_threshold: float = 0.75
    tagger_rating_threshold: float = 0.0
    tagger_include_characters: bool = True
    tagger_include_rating: bool = False
    tagger_top_k: int = 128
    tagger_blacklist: list[str] | None = None
    tagger_aliases: dict[str, str] | None = None


class AutoPlanService:
    def __init__(self, projects: ProjectService):
        self.projects = projects

    def build(self, project_id: str, request: AutoPlanRequest) -> dict[str, Any]:
        project = self.projects.get_project(project_id)
        if project.last_scan is None:
            project = self.projects.scan_project(project_id)
        scan = project.last_scan
        assert scan is not None
        if request.scope == "vlm_augment":
            create, overwrite, preserve = 0, scan.captions, scan.missing_captions
        elif request.scope in {"all", "augment"}:
            create, overwrite, preserve = scan.missing_captions, scan.captions, 0
        else:
            create, overwrite, preserve = scan.missing_captions, 0, scan.captions
        affected = create + overwrite
        preflight_blockers: list[str] = []
        if affected and not os.access(project.dataset_path, os.W_OK):
            preflight_blockers.append("Нет прав на запись в папку dataset")
        try:
            free_bytes = shutil.disk_usage(project.dataset_path).free
            required_bytes = max(50 * 1024 * 1024, affected * 128 * 1024)
            if affected and free_bytes < required_bytes:
                preflight_blockers.append("Недостаточно свободного места для безопасного сохранения результатов")
        except OSError:
            preflight_blockers.append("Не удалось проверить свободное место в папке dataset")
        settings = load_settings()
        backend_type = str(settings.get("backend_type", config.DEFAULT_BACKEND_TYPE))
        if request.pipeline_mode == "tagger_only":
            model_snapshot = {"backend_type": "none", "model": "tagger-only"}
            missing = []
        elif backend_type == "managed_llama":
            model_snapshot = {
                "backend_type": "managed_llama", "executable": str(settings.get("llama_executable", config.DEFAULT_LLAMA_EXECUTABLE)),
                "model_path": str(settings.get("llama_model", config.DEFAULT_LLAMA_MODEL)),
                "mmproj_path": str(settings.get("llama_mmproj", config.DEFAULT_LLAMA_MMPROJ)),
                "host": str(settings.get("llama_host", config.DEFAULT_LLAMA_HOST)), "port": int(settings.get("llama_port", config.DEFAULT_LLAMA_PORT)),
                "api_prefix": str(settings.get("llama_api_prefix", config.DEFAULT_LLAMA_API_PREFIX)),
                "startup_timeout": float(settings.get("llama_startup_timeout", config.DEFAULT_LLAMA_STARTUP_TIMEOUT)),
                "timeout": float(settings.get("timeout", config.DEFAULT_TIMEOUT)),
            }
            missing = [label for label, path in (("llama-server", model_snapshot["executable"]), ("VLM model", model_snapshot["model_path"]), ("mmproj", model_snapshot["mmproj_path"])) if not path or not os.path.isfile(str(path))]
        else:
            model_snapshot = {"backend_type":"external","base_url":str(settings.get("api_url",config.DEFAULT_API_URL)),"model":str(settings.get("model",config.DEFAULT_MODEL)),"timeout":float(settings.get("timeout",config.DEFAULT_TIMEOUT))}
            missing = []
        recipe_draft = {
            "goal": request.focus, "result_type": request.result_type, "language": request.language,
            "detail": request.detail, "trigger_word": request.trigger_word, "review_policy": request.review_policy,
            "system_prompt": request.system_prompt if request.system_prompt is not None else config.DEFAULT_SYSTEM_PROMPT,
            "user_prompt": request.user_prompt if request.user_prompt is not None else config.DEFAULT_USER_PROMPT,
            "temperature": float(request.temperature if request.temperature is not None else settings.get("temperature", config.DEFAULT_TEMPERATURE)),
            "max_tokens": int(request.max_tokens if request.max_tokens is not None else settings.get("max_tokens", config.DEFAULT_MAX_TOKENS)),
            "top_p": float(request.top_p if request.top_p is not None else settings.get("top_p", config.DEFAULT_TOP_P)),
            "context_size": int(request.context_size if request.context_size is not None else settings.get("llama_context_size", 8192)),
            "auto_retry": request.auto_retry if request.auto_retry is not None else bool(settings.get("auto_retry", True)),
            # Dataset captioning needs concise visible output; inheriting a legacy
            # chat/reasoning preference can waste the whole token budget before a caption.
            "disable_thinking": request.analysis_mode == "fast",
            "reasoning_budget": 0 if request.analysis_mode == "fast" else (request.reasoning_budget if request.reasoning_budget is not None else 1024),
            "pipeline_tagger_ids": request.pipeline_tagger_ids if request.pipeline_tagger_ids is not None else list(settings.get("pipeline_tagger_ids", [])),
            "pipeline_mode": request.pipeline_mode,
            "tagger_write_mode": request.scope,
            "caption_write_mode": request.scope,
            "include_subfolders": bool(project.settings.get("include_subfolders", False)),
            "tagger_policy": {
                "general_threshold": request.tagger_general_threshold,
                "character_threshold": request.tagger_character_threshold,
                "rating_threshold": request.tagger_rating_threshold,
                "include_characters": request.tagger_include_characters,
                "include_rating": request.tagger_include_rating,
                "top_k": request.tagger_top_k,
                "blacklist": request.tagger_blacklist or [],
                "aliases": request.tagger_aliases or {},
            },
        }
        if request.pipeline_mode != "vlm" and not recipe_draft["pipeline_tagger_ids"]:
            preflight_blockers.append("Не выбрана Tagger-модель")
        scope_payload = {"create": create, "overwrite": overwrite,
                         "preserve": preserve, "total": scan.images}
        if request.scope == "vlm_augment":
            scope_payload["mode"] = request.scope
        return {
            "plan_id": uuid4().hex,
            "project_id": project_id,
            "scope": scope_payload,
            "recipe_draft": recipe_draft,
            "model_snapshot": model_snapshot,
            "effective_resource_configuration": {
                "profile": "manual",
                "context_size": recipe_draft["context_size"],
                "keep_alive_seconds": int(request.keep_alive_seconds or 0),
                "runtime": {
                    "gpu_layers": request.gpu_layers,
                    "threads": request.threads,
                    "batch_size": request.batch_size,
                    "ubatch_size": request.ubatch_size,
                    "cache_type_k": request.cache_type_k,
                    "cache_type_v": request.cache_type_v,
                    "flash_attention": request.flash_attention,
                    "mmap": request.mmap,
                    "slots": request.slots,
                    "additional_args": request.additional_args or [],
                },
                "llama_args": self._llama_args(request),
            },
            "resource_plan": {"profile": "balanced", "model_selection": "automatic"},
            "estimated_duration": None,
            "memory_risk": "unknown_until_model_selected",
            "warnings": (["Будут перезаписаны существующие captions"] if overwrite and request.scope == "all" else
                         ["Существующие captions будут дополнены тегами"] if overwrite and request.scope == "augment" else []),
            "blockers": (["Нет изображений для обработки в выбранной области"] if not affected else []) + (["Не настроены: " + ", ".join(missing)] if missing else []) + preflight_blockers,
            "alternatives": ([{"scope": "missing", "label": "Обработать только изображения без captions"}] if overwrite else []),
            "explanation": {
                "model": "Модель будет выбрана после проверки доступных локальных ресурсов.",
                "resources": "Сбалансированный профиль выбран как безопасная отправная точка.",
                "tradeoff": "Профиль отдаёт приоритет надёжности и предсказуемому использованию памяти.",
                "confidence": "preliminary",
            },
            "test_recommended": True,
        }

    @staticmethod
    def _llama_args(request: AutoPlanRequest) -> list[str]:
        args: list[str] = ["--ctx-size", str(request.context_size or 8192)]
        optional = (
            ("--n-gpu-layers", request.gpu_layers),
            ("--threads", request.threads),
            ("--batch-size", request.batch_size),
            ("--ubatch-size", request.ubatch_size),
        )
        for flag, value in optional:
            if value is not None:
                args.extend((flag, str(value)))
        args.extend(("--cache-type-k", request.cache_type_k, "--cache-type-v", request.cache_type_v,
                     "--parallel", str(request.slots)))
        if request.flash_attention:
            args.append("--flash-attn")
        if not request.mmap:
            args.append("--no-mmap")
        args.extend(request.additional_args or [])
        return args
