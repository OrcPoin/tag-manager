from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import SCHEMA_VERSION, utc_now


@dataclass(slots=True)
class ProjectScan:
    images: int = 0
    root_images: int = 0
    nested_images: int = 0
    captions: int = 0
    missing_captions: int = 0
    unsupported: int = 0
    scanned_at: str = field(default_factory=utc_now)
    signature: str = ""


@dataclass(slots=True)
class Project:
    id: str
    name: str
    dataset_path: str
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    active_recipe_id: str | None = None
    active_recipe_version: int | None = None
    review_policy: str = "queue"
    status: str = "ready"
    notes: str = ""
    attention_summary: dict[str, int] = field(default_factory=dict)
    last_scan: ProjectScan | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    run_refs: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        values = dict(data)
        scan = values.get("last_scan")
        if isinstance(scan, dict):
            values["last_scan"] = ProjectScan(**scan)
        return cls(**values)
