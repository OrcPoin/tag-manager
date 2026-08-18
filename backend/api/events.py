from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.api.projects import project_service
from backend.services import ProjectService


router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
def list_events(
    after: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    service: ProjectService = Depends(project_service),
):
    return {"events": service.list_events(after=after, limit=limit)}
