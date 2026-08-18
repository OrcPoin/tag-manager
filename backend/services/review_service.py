from __future__ import annotations

from uuid import uuid4

from backend.domain import Event, ReviewItem, ReviewStatus
from backend.domain.common import utc_now
from backend.persistence import EventStore, ProjectRepository

from .project_service import ProjectService


class ReviewItemNotFoundError(LookupError):
    pass


class InvalidReviewDecisionError(ValueError):
    pass


class ReviewService:
    def __init__(self, projects: ProjectService):
        self.projects = projects

    def list(self, project_id: str, status: ReviewStatus | None = None) -> list[ReviewItem]:
        project = self.projects.get_project(project_id)
        items = ProjectRepository(project.dataset_path).load_review_queue()
        return [item for item in items if status is None or item.status is status]

    def get(self, item_id: str) -> ReviewItem:
        return self._find(item_id)[3]

    def enqueue(self, project_id: str, run_id: str, image_relative_path: str,
                proposed_caption: str, reason_codes: list[str], reasons: list[str],
                model_confidence: float | None = None) -> ReviewItem:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        items = repository.load_review_queue()
        item = ReviewItem(uuid4().hex, project_id, run_id, image_relative_path,
                          proposed_caption, reason_codes, reasons, model_confidence)
        items.append(item)
        repository.save_review_queue(items)
        self._event(repository, item, "review.queued", "Результат добавлен в очередь проверки")
        return item

    def decide(self, item_id: str, action: ReviewStatus, idempotency_key: str,
               caption: str | None = None, note: str = "") -> ReviewItem:
        if action is ReviewStatus.PENDING:
            raise InvalidReviewDecisionError("Pending is not a decision")
        project, repository, items, item = self._find(item_id)
        resource = self.projects.index.remember_command(idempotency_key, f"review.{action.value}", item_id)
        if resource != item_id:
            raise InvalidReviewDecisionError("Idempotency key belongs to another review item")
        if any(entry.get("idempotency_key") == idempotency_key for entry in item.decision_history):
            return item
        if item.status is not ReviewStatus.PENDING and action is not ReviewStatus.REGENERATE_REQUESTED:
            raise InvalidReviewDecisionError(f"Review item already has decision: {item.status.value}")
        if action is ReviewStatus.EDITED:
            if not caption or not caption.strip():
                raise InvalidReviewDecisionError("Edited caption cannot be empty")
            item.proposed_caption = caption.strip()
        item.status = action
        item.user_note = note
        item.decision_history.append({
            "action": action.value,
            "at": utc_now(),
            "note": note,
            "idempotency_key": idempotency_key,
        })
        repository.save_review_queue(items)
        labels = {
            ReviewStatus.ACCEPTED: "Результат принят",
            ReviewStatus.EDITED: "Исправленный результат принят",
            ReviewStatus.REGENERATE_REQUESTED: "Запрошена повторная генерация",
            ReviewStatus.SKIPPED: "Результат пропущен",
        }
        self._event(repository, item, f"review.{action.value}", labels[action])
        return item

    def _find(self, item_id: str):
        for project in self.projects.list_projects():
            repository = ProjectRepository(project.dataset_path)
            items = repository.load_review_queue()
            for item in items:
                if item.id == item_id:
                    return project, repository, items, item
        raise ReviewItemNotFoundError(item_id)

    @staticmethod
    def _event(repository: ProjectRepository, item: ReviewItem, event_type: str, message: str) -> None:
        EventStore(repository.sidecar_path / "runs", item.run_id).append(
            Event(event_type, item.project_id, item.run_id, message, {"review_item_id": item.id, "status": item.status.value})
        )
