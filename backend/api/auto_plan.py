from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from pathlib import Path
from core.app_settings import load_settings, save_settings
from core.taggers.manager import TaggerManager
from core.taggers.registry import TAGGER_SPECS

from backend.services import AutoPlanRequest, AutoPlanService
from backend.services.project_service import ProjectNotFoundError


router = APIRouter(tags=["auto-plan"])


class BuildAutoPlanRequest(BaseModel):
    result_type: str = Field(default="hybrid_caption", pattern="^(hybrid_caption|tags|prose)$")
    scope: str = Field(default="missing", pattern="^(missing|augment|vlm_augment|all)$")
    focus: str = Field(default="balanced", max_length=500)
    detail: str = Field(default="balanced", pattern="^(concise|balanced|detailed)$")
    language: str = Field(default="ru", max_length=20)
    trigger_word: str = Field(default="", max_length=200)
    review_policy: str = Field(default="queue", pattern="^(queue|stop_on_review)$")
    analysis_mode: str = Field(default="fast", pattern="^(fast|accurate)$")
    reasoning_budget: int | None = Field(default=None, ge=0, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=128, le=32768)
    context_size: int | None = Field(default=None, ge=1024, le=131072)
    system_prompt: str | None = Field(default=None, max_length=30000)
    user_prompt: str | None = Field(default=None, max_length=30000)
    keep_alive_seconds: int | None = Field(default=None, ge=0, le=86400)
    auto_retry: bool | None = None
    pipeline_tagger_ids: list[str] | None = None
    pipeline_mode: str = Field(default="vlm", pattern="^(vlm|tagger_vlm|tagger_only)$")
    gpu_layers: int | None = Field(default=None, ge=0, le=999)
    threads: int | None = Field(default=None, ge=1, le=512)
    batch_size: int | None = Field(default=None, ge=1, le=65536)
    ubatch_size: int | None = Field(default=None, ge=1, le=65536)
    cache_type_k: str = Field(default="f16", pattern="^(f32|f16|bf16|q8_0|q4_0)$")
    cache_type_v: str = Field(default="f16", pattern="^(f32|f16|bf16|q8_0|q4_0)$")
    flash_attention: bool = True
    mmap: bool = True
    slots: int = Field(default=1, ge=1, le=32)
    additional_args: list[str] | None = Field(default=None, max_length=64)
    tagger_general_threshold: float = Field(default=0.35, ge=0, le=1)
    tagger_character_threshold: float = Field(default=0.75, ge=0, le=1)
    tagger_rating_threshold: float = Field(default=0.0, ge=0, le=1)
    tagger_include_characters: bool = True
    tagger_include_rating: bool = False
    tagger_top_k: int = Field(default=128, ge=1, le=4096)
    tagger_blacklist: list[str] | None = Field(default=None, max_length=4096)
    tagger_aliases: dict[str, str] | None = None


class ConfigureModelsRequest(BaseModel):
    vlm_path: str = Field(min_length=1)
    mmproj_path: str = Field(min_length=1)


def auto_plan_service(request: Request) -> AutoPlanService:
    return request.app.state.auto_plan_service


@router.post("/api/models/configure")
def configure_models(body: ConfigureModelsRequest):
    paths = {"VLM model": Path(body.vlm_path).resolve(), "mmproj": Path(body.mmproj_path).resolve()}
    invalid = [name for name, path in paths.items() if not path.is_file() or path.suffix.lower() != ".gguf"]
    if invalid:
        raise HTTPException(400, detail={"code": "invalid_model_files",
            "message": "Не найдены корректные GGUF: " + ", ".join(invalid),
            "recovery_action": "Выберите существующие файлы .gguf"})
    settings = load_settings()
    settings.update(llama_model=str(paths["VLM model"]), llama_mmproj=str(paths["mmproj"]),
                    model_directory=str(paths["VLM model"].parent),
                    mmproj_directory=str(paths["mmproj"].parent), backend_type="managed_llama")
    save_settings(settings)
    return {"status": "configured", "vlm_path": str(paths["VLM model"]),
            "mmproj_path": str(paths["mmproj"])}


@router.get("/api/taggers")
def list_taggers():
    manager = TaggerManager("taggers")
    return [{"id": key, "name": spec.display_name, "installed": manager.installed(key)}
            for key, spec in TAGGER_SPECS.items()]


@router.post("/api/projects/{project_id}/auto-plan")
def build_auto_plan(project_id: str, body: BuildAutoPlanRequest, service: AutoPlanService = Depends(auto_plan_service)):
    try:
        return service.build(project_id, AutoPlanRequest(**body.model_dump()))
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден", "recovery_action": "Откройте dataset заново"}) from error
