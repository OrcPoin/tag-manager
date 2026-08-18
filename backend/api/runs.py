from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.domain.common import to_primitive
from backend.services.run_service import InvalidRunTransitionError, RunNotFoundError, RunService
from backend.services.run_coordinator import RunCoordinator, RunExecutorUnavailable


router = APIRouter(tags=["runs"])


class CreateRunRequest(BaseModel):
    scope_plan: dict = Field(default_factory=dict)
    recipe_snapshot: dict = Field(default_factory=dict)
    model_snapshot: dict = Field(default_factory=dict)
    effective_resource_configuration: dict = Field(default_factory=dict)

class RepeatRunRequest(BaseModel):
    scope_plan: dict | None = None


def run_service(request: Request) -> RunService:
    return request.app.state.run_service


def run_coordinator(request: Request) -> RunCoordinator:
    return request.app.state.run_coordinator


@router.get("/api/runs/active")
def active_runs(service: RunService = Depends(run_service)):
    return [to_primitive(run) for run in service.active_runs()]


def _run_command(service: RunService, run_id: str, action: str, key: str):
    try:
        return {"command_id": key, "state": to_primitive(service.execute_command(run_id, action, key))}
    except RunNotFoundError as error:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Запуск не найден", "recovery_action": "Откройте проект заново"}) from error
    except InvalidRunTransitionError as error:
        raise HTTPException(409, detail={"code": "invalid_run_transition", "message": str(error), "recovery_action": "Обновите состояние запуска"}) from error
    except ValueError as error:
        raise HTTPException(409, detail={"code": "idempotency_conflict", "message": str(error), "recovery_action": "Используйте новый ключ команды"}) from error


@router.post("/api/projects/{project_id}/runs")
def create_run(project_id: str, body: CreateRunRequest, idempotency_key: str = Header(min_length=8), service: RunService = Depends(run_service)):
    try:
        run = service.create(project_id, body.scope_plan, body.recipe_snapshot, idempotency_key,
                             body.model_snapshot, body.effective_resource_configuration)
        return {"command_id": idempotency_key, "state": to_primitive(run)}
    except LookupError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден", "recovery_action": "Откройте dataset заново"}) from error
    except ValueError as error:
        raise HTTPException(409, detail={"code": "idempotency_conflict", "message": str(error), "recovery_action": "Используйте новый ключ команды"}) from error


@router.get("/api/projects/{project_id}/runs")
def list_project_runs(project_id: str, status: str | None = None, recipe_hash: str | None = None, service: RunService = Depends(run_service)):
    try:
        return [to_primitive(run) for run in service.list_project(project_id, status=status, recipe_hash=recipe_hash)]
    except LookupError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден"}) from error


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, service: RunService = Depends(run_service)):
    try:
        return to_primitive(service.get(run_id))
    except RunNotFoundError as error:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Запуск не найден", "recovery_action": "Откройте проект заново"}) from error


@router.get("/api/runs/{run_id}/summary")
def get_run_summary(run_id: str, service: RunService = Depends(run_service)):
    try:
        return service.summary(run_id)
    except RunNotFoundError as error:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Запуск не найден", "recovery_action": "Откройте проект заново"}) from error


@router.post("/api/runs/{run_id}/repeat")
def repeat_run(run_id: str, body: RepeatRunRequest, idempotency_key: str = Header(min_length=8), service: RunService = Depends(run_service)):
    try:
        return {"command_id": idempotency_key, "state": to_primitive(service.repeat_configuration(run_id, idempotency_key, body.scope_plan))}
    except RunNotFoundError as error:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Run not found"}) from error


@router.get("/api/runs/{run_id}/compare/{other_run_id}")
def compare_runs(run_id: str, other_run_id: str, service: RunService = Depends(run_service)):
    try:
        return service.compare(run_id, other_run_id)
    except RunNotFoundError as error:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Run not found"}) from error
    except ValueError as error:
        raise HTTPException(409, detail={"code": "compare_rejected", "message": str(error)}) from error


@router.post("/api/runs/{run_id}/start")
def start_run(run_id: str, idempotency_key: str = Header(min_length=8), coordinator: RunCoordinator = Depends(run_coordinator)):
    try:
        return {"command_id": idempotency_key, "state": to_primitive(coordinator.start(run_id, idempotency_key))}
    except RunExecutorUnavailable as error:
        raise HTTPException(503, detail={"code": "inference_unavailable", "message": str(error), "recovery_action": "Настройте локальную модель"}) from error
    except (RunNotFoundError, InvalidRunTransitionError, ValueError) as error:
        raise HTTPException(409, detail={"code": "run_start_rejected", "message": str(error), "recovery_action": "Обновите состояние запуска"}) from error

@router.post("/api/runs/{run_id}/resume-remaining")
def resume_remaining(run_id: str, idempotency_key: str = Header(min_length=8),
                     service: RunService = Depends(run_service), coordinator: RunCoordinator = Depends(run_coordinator)):
    try:
        run = service.remaining_plan(run_id, idempotency_key)
        started = coordinator.start(run.run_id, idempotency_key + "-start")
        return {"command_id": idempotency_key, "state": to_primitive(started)}
    except RunExecutorUnavailable as error:
        raise HTTPException(503, detail={"code": "inference_unavailable", "message": str(error)}) from error
    except (RunNotFoundError, InvalidRunTransitionError, ValueError) as error:
        raise HTTPException(409, detail={"code": "resume_rejected", "message": str(error)}) from error


@router.post("/api/runs/{run_id}/pause")
def pause_run(run_id: str, idempotency_key: str = Header(min_length=8), service: RunService = Depends(run_service)):
    return _run_command(service, run_id, "pause", idempotency_key)


@router.post("/api/runs/{run_id}/resume")
def resume_run(run_id: str, idempotency_key: str = Header(min_length=8), service: RunService = Depends(run_service)):
    return _run_command(service, run_id, "resume", idempotency_key)


@router.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str, idempotency_key: str = Header(min_length=8), service: RunService = Depends(run_service)):
    return _run_command(service, run_id, "stop", idempotency_key)


@router.post("/api/runs/{run_id}/review-each/disable")
def disable_review_each(run_id: str, service: RunService = Depends(run_service)):
    try:
        return {"state": to_primitive(service.disable_review_each(run_id))}
    except RunNotFoundError as error:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Запуск не найден"}) from error
