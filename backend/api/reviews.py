from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.domain import ReviewStatus
from backend.domain.common import to_primitive
from backend.services.project_service import ProjectNotFoundError
from backend.services.review_service import InvalidReviewDecisionError, ReviewItemNotFoundError, ReviewService


router = APIRouter(tags=["reviews"])


class ReviewDecisionRequest(BaseModel):
    caption: str | None = Field(default=None, max_length=100_000)
    note: str = Field(default="", max_length=20_000)


def review_service(request: Request) -> ReviewService:
    return request.app.state.review_service


@router.get("/api/projects/{project_id}/review")
def list_review(project_id: str, status: ReviewStatus | None = Query(default=None), service: ReviewService = Depends(review_service)):
    try:
        return [to_primitive(item) for item in service.list(project_id, status)]
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"code": "project_not_found", "message": "Проект не найден", "recovery_action": "Откройте dataset заново"}) from error


def _decide(service: ReviewService, item_id: str, action: ReviewStatus, key: str, body: ReviewDecisionRequest):
    try:
        return {"command_id": key, "state": to_primitive(service.decide(item_id, action, key, body.caption, body.note))}
    except ReviewItemNotFoundError as error:
        raise HTTPException(404, detail={"code": "review_not_found", "message": "Элемент проверки не найден", "recovery_action": "Обновите очередь"}) from error
    except (InvalidReviewDecisionError, ValueError) as error:
        raise HTTPException(409, detail={"code": "invalid_review_decision", "message": str(error), "recovery_action": "Обновите очередь и повторите решение"}) from error


@router.post("/api/review/{item_id}/accept")
def accept(item_id: str, body: ReviewDecisionRequest, idempotency_key: str = Header(min_length=8), service: ReviewService = Depends(review_service)):
    return _decide(service, item_id, ReviewStatus.ACCEPTED, idempotency_key, body)


@router.post("/api/review/{item_id}/edit")
def edit(item_id: str, body: ReviewDecisionRequest, idempotency_key: str = Header(min_length=8), service: ReviewService = Depends(review_service)):
    return _decide(service, item_id, ReviewStatus.EDITED, idempotency_key, body)


@router.post("/api/review/{item_id}/regenerate")
def regenerate(item_id: str, body: ReviewDecisionRequest, idempotency_key: str = Header(min_length=8), service: ReviewService = Depends(review_service)):
    return _decide(service, item_id, ReviewStatus.REGENERATE_REQUESTED, idempotency_key, body)


@router.post("/api/review/{item_id}/skip")
def skip(item_id: str, body: ReviewDecisionRequest, idempotency_key: str = Header(min_length=8), service: ReviewService = Depends(review_service)):
    return _decide(service, item_id, ReviewStatus.SKIPPED, idempotency_key, body)
