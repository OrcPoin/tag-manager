from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .common import SCHEMA_VERSION, utc_now


class ReviewStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EDITED = "edited"
    REGENERATE_REQUESTED = "regenerate_requested"
    SKIPPED = "skipped"


@dataclass(slots=True)
class ReviewItem:
    id: str
    project_id: str
    run_id: str
    image_relative_path: str
    proposed_caption: str
    reason_codes: list[str]
    reasons: list[str]
    model_confidence: float | None = None
    status: ReviewStatus = ReviewStatus.PENDING
    user_note: str = ""
    decision_history: list[dict[str, str]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewItem":
        values = dict(data)
        values["status"] = ReviewStatus(values.get("status", ReviewStatus.PENDING))
        return cls(**values)
