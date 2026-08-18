from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel, Field
from uuid import uuid4

from backend.domain.common import to_primitive
from backend.services.project_service import ProjectNotFoundError, ProjectService
from backend.persistence import EventRetention, ProjectRepository
from backend.persistence.atomic import atomic_write_text
from backend.services import AutoPlanRequest


router = APIRouter(prefix="/api/projects", tags=["projects"])


class OpenProjectRequest(BaseModel):
    dataset_path: str = Field(min_length=1)


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    review_policy: str | None = Field(default=None, pattern="^(queue|stop_on_review)$")
    status: str | None = Field(default=None, pattern="^(ready|attention|archived)$")
    notes: str | None = Field(default=None, max_length=20_000)


class ScanProjectRequest(BaseModel):
    include_subfolders: bool | None = None


class CaptionUpdateRequest(BaseModel):
    caption: str = Field(max_length=100_000)


class GalleryRegenerateRequest(BaseModel):
    path: str = Field(default="", max_length=4000)
    paths: list[str] = Field(default_factory=list, max_length=1000)
    feedback: str = Field(default="", max_length=4000)


class GalleryBulkRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=1000)
    action: str = Field(pattern="^(add_tag|remove_tag|clear_caption)$")
    value: str = Field(default="", max_length=500)

class VisualSearchRequest(BaseModel):
    references: list[str] = Field(min_length=1, max_length=12)
    limit: int = Field(default=100, ge=1, le=500)
    threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    mode: str = Field(default="overall", pattern="^(overall|pose_action|composition|theme)$")
    query: str = Field(default="", max_length=1000)


def _project_image_path(project_id: str, relative: str, service: ProjectService) -> tuple[Path, Path]:
    project = service.get_project(project_id)
    root = Path(project.dataset_path).resolve()
    target = (root / relative).resolve()
    if root not in target.parents or target.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or not target.is_file():
        raise FileNotFoundError(relative)
    return root, target


def project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


@router.get("")
def list_projects(service: ProjectService = Depends(project_service)):
    return [to_primitive(project) for project in service.list_projects()]


@router.post("/open")
def open_project(command: OpenProjectRequest, service: ProjectService = Depends(project_service)):
    try:
        return to_primitive(service.open_project(command.dataset_path))
    except NotADirectoryError as error:
        raise HTTPException(400, detail={"code": "dataset_not_found", "message": "Папка dataset не найдена", "recovery_action": "Выберите существующую папку"}) from error


@router.get("/{project_id}")
def get_project(project_id: str, service: ProjectService = Depends(project_service)):
    try:
        return to_primitive(service.get_project(project_id))
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден", "recovery_action": "Откройте dataset заново"}) from error


@router.patch("/{project_id}")
def update_project(project_id: str, command: UpdateProjectRequest, service: ProjectService = Depends(project_service)):
    try:
        changes = command.model_dump(exclude_none=True)
        return to_primitive(service.update_project(project_id, changes))
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден", "recovery_action": "Откройте dataset заново"}) from error


@router.post("/{project_id}/scan")
def scan_project(project_id: str, command: ScanProjectRequest | None = None,
                 service: ProjectService = Depends(project_service)):
    try:
        return to_primitive(service.scan_project(
            project_id, command.include_subfolders if command else None))
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден", "recovery_action": "Откройте dataset заново"}) from error


@router.get("/{project_id}/image")
def project_image(project_id: str, path: str = Query(min_length=1), service: ProjectService = Depends(project_service)):
    try:
        _, target = _project_image_path(project_id, path, service)
        return FileResponse(target)
    except (ProjectNotFoundError, FileNotFoundError) as error:
        raise HTTPException(404, detail={"code": "image_not_found", "message": "Изображение не найдено", "recovery_action": "Пересканируйте dataset"}) from error


@router.put("/{project_id}/caption")
def update_caption(project_id: str, path: str, body: CaptionUpdateRequest,
                   service: ProjectService = Depends(project_service)):
    try:
        _, image = _project_image_path(project_id, path, service)
        atomic_write_text(image.with_suffix(".txt"), body.caption)
        return {"path": path, "caption": body.caption.strip(), "has_caption": bool(body.caption.strip())}
    except (ProjectNotFoundError, FileNotFoundError) as error:
        raise HTTPException(404, detail={"code": "image_not_found", "message": "Изображение не найдено"}) from error


@router.post("/{project_id}/gallery/regenerate")
def regenerate_gallery_item(project_id: str, body: GalleryRegenerateRequest, request: Request,
                            service: ProjectService = Depends(project_service)):
    try:
        requested = body.paths or ([body.path] if body.path else [])
        if not requested:
            raise HTTPException(400, detail={"code": "empty_selection", "message": "Выберите изображения"})
        resolved = [_project_image_path(project_id, relative, service) for relative in requested]
        root = resolved[0][0]
        images = [item[1] for item in resolved]
        plan = request.app.state.auto_plan_service.build(project_id, AutoPlanRequest(scope="all"))
        if plan["blockers"]:
            raise HTTPException(409, detail={"code": "regenerate_blocked", "message": "; ".join(plan["blockers"])})
        if body.feedback.strip():
            plan["recipe_draft"]["user_prompt"] += "\n\nCorrection request for this image: " + body.feedback.strip()
        relatives = [image.relative_to(root).as_posix() for image in images]
        existing = sum(image.with_suffix(".txt").is_file() for image in images)
        scope = {"mode": "all", "images": relatives, "create": len(images) - existing,
                 "overwrite": existing, "preserve": 0, "total": len(images),
                 "include_subfolders": bool(plan["recipe_draft"].get("include_subfolders", False))}
        key = "gallery-" + uuid4().hex
        run = request.app.state.run_service.create(project_id, scope, plan["recipe_draft"], key,
                                                   plan["model_snapshot"], plan["effective_resource_configuration"])
        started = request.app.state.run_coordinator.start(run.run_id, key + "-start")
        return {"run_id": started.run_id, "status": started.status.value}
    except (ProjectNotFoundError, FileNotFoundError) as error:
        raise HTTPException(404, detail={"code": "image_not_found", "message": "Изображение не найдено"}) from error


@router.post("/{project_id}/gallery/bulk")
def gallery_bulk(project_id: str, body: GalleryBulkRequest,
                 service: ProjectService = Depends(project_service)):
    from core.dataset import add_tag_to_caption, remove_tag_from_caption
    updated = []
    try:
        for relative in body.paths:
            _, image = _project_image_path(project_id, relative, service)
            caption_path = image.with_suffix(".txt")
            try:
                current = caption_path.read_text(encoding="utf-8") if caption_path.is_file() else ""
            except (OSError, UnicodeDecodeError):
                current = ""
            if body.action == "add_tag":
                if not body.value.strip():
                    raise HTTPException(400, detail={"code": "tag_required", "message": "Укажите тег"})
                next_caption = add_tag_to_caption(current, body.value, at_start=False)
            elif body.action == "remove_tag":
                if not body.value.strip():
                    raise HTTPException(400, detail={"code": "tag_required", "message": "Укажите тег"})
                next_caption = remove_tag_from_caption(current, body.value)
            else:
                next_caption = ""
            atomic_write_text(caption_path, next_caption)
            updated.append({"path": relative, "caption": next_caption.strip(), "has_caption": bool(next_caption.strip())})
        return {"updated": updated}
    except (ProjectNotFoundError, FileNotFoundError) as error:
        raise HTTPException(404, detail={"code": "image_not_found", "message": "Изображение не найдено"}) from error


@router.get("/{project_id}/gallery")
def project_gallery(project_id: str, search: str = "", missing_only: bool = False,
                    page: int = Query(default=1, ge=1), page_size: int = Query(default=60, ge=12, le=200),
                    service: ProjectService = Depends(project_service)):
    try:
        return service.gallery(project_id, search=search, missing_only=missing_only,
                               page=page, page_size=page_size)
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден"}) from error


@router.post("/{project_id}/visual-search")
def visual_search(project_id: str, body: VisualSearchRequest, request: Request, service: ProjectService = Depends(project_service)):
    from core.visual_search import VisualSearchIndex
    try:
        project = service.get_project(project_id)
        embedder = request.app.state.visual_model_manager.create_embedder(project.settings.get("visual_search_model", "clip-vit-base-patch32")) if hasattr(request.app.state, "visual_model_manager") else None
        return VisualSearchIndex(project.dataset_path, bool(project.settings.get("include_subfolders", True)), embedder).search(body.references, body.limit, body.threshold, body.mode, body.query)
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Project not found"}) from error
    except FileNotFoundError as error:
        raise HTTPException(404, detail={"code": "reference_not_found", "message": "Reference image not found"}) from error

@router.post("/{project_id}/visual-search/index")
def rebuild_visual_search_index(project_id: str, service: ProjectService = Depends(project_service)):
    from core.visual_search import VisualSearchIndex
    try:
        project = service.get_project(project_id)
        return VisualSearchIndex(project.dataset_path, bool(project.settings.get("include_subfolders", True))).build(force=True)
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Project not found"}) from error

@router.post("/{project_id}/health")
def project_health(project_id: str, service: ProjectService = Depends(project_service)):
    try:
        return service.health(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден"}) from error


@router.get("/{project_id}/storage")
def project_storage(project_id: str, service: ProjectService = Depends(project_service)):
    try:
        project = service.get_project(project_id)
        return EventRetention.usage(ProjectRepository(project.dataset_path))
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден"}) from error


@router.post("/{project_id}/storage/cleanup")
def cleanup_project_storage(project_id: str, service: ProjectService = Depends(project_service)):
    try:
        project = service.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        removed = EventRetention().rotate(repository)
        return {"removed_detail_for_runs": removed, "usage": EventRetention.usage(repository)}
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден"}) from error
