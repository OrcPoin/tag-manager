from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .common import SCHEMA_VERSION, utc_now


@dataclass(slots=True)
class Event:
    type: str
    project_id: str
    run_id: str | None
    message: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: int = SCHEMA_VERSION
    ts: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        family = self.type.partition(".")[0]
        if family not in {"project", "run", "model", "review", "quality", "recipe", "system"}:
            raise ValueError(f"Unsupported event family: {family}")
