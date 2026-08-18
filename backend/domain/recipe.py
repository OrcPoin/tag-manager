from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import hashlib
import json

from .common import SCHEMA_VERSION, to_primitive


class RecipeStatus(str, Enum):
    DRAFT = "draft"
    IMMUTABLE = "immutable"
    ARCHIVED = "archived"


@dataclass(slots=True)
class RecipeVersion:
    recipe_id: str
    version: int
    goal: str
    result_type: str
    status: RecipeStatus = RecipeStatus.DRAFT
    parent_version: int | None = None
    origin_recipe_id: str | None = None
    prompt: str = ""
    instructions: str = ""
    pipeline_stages: list[dict[str, Any]] = field(default_factory=list)
    quality_policy: dict[str, Any] = field(default_factory=dict)
    generation_settings: dict[str, Any] = field(default_factory=dict)
    compatible_model_constraints: dict[str, Any] = field(default_factory=dict)
    test_result: dict[str, Any] | None = None
    content_hash: str = ""
    schema_version: int = SCHEMA_VERSION

    def assert_editable(self) -> None:
        if self.status is not RecipeStatus.DRAFT:
            raise ValueError("Used or archived recipe versions are immutable")

    def refresh_content_hash(self) -> str:
        content = to_primitive(self)
        content.pop("content_hash", None)
        content.pop("status", None)
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.content_hash = hashlib.sha256(encoded).hexdigest()
        return self.content_hash

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecipeVersion":
        values = dict(data)
        values["status"] = RecipeStatus(values.get("status", RecipeStatus.DRAFT))
        return cls(**values)
