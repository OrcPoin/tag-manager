from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services import TestRunService
from backend.services.project_service import ProjectNotFoundError


router = APIRouter(tags=["test-run"])


class TestRunRequest(BaseModel):
    recipe_snapshot: dict = Field(default_factory=dict)
    model_snapshot: dict = Field(default_factory=dict)
    effective_resource_configuration: dict = Field(default_factory=dict)


def get_test_run_service(request: Request) -> TestRunService:
    return request.app.state.test_run_service


@router.post("/api/projects/{project_id}/test-run")
def execute_test_run(project_id: str, body: TestRunRequest,
             idempotency_key: str = Header(min_length=8),
             service: TestRunService = Depends(get_test_run_service)):
    try:
        state = service.execute(project_id, body.recipe_snapshot, idempotency_key,
                                body.model_snapshot, body.effective_resource_configuration)
        return {"command_id": idempotency_key, "state": state}
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден", "recovery_action": "Откройте dataset заново"}) from error
    except ValueError as error:
        raise HTTPException(409, detail={"code": "idempotency_conflict", "message": str(error), "recovery_action": "Используйте новый ключ команды"}) from error
