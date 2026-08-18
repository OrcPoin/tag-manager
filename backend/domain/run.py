from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .common import SCHEMA_VERSION, utc_now


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOP_REQUESTED = "stop_requested"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStage(str, Enum):
    DISCOVERY = "discovery"
    MODEL_PREPARATION = "model_preparation"
    TAGGER = "tagger"
    VLM = "vlm"
    WRITING = "writing"
    UNLOADING = "unloading"
    FINISHED = "finished"


@dataclass(slots=True)
class RunProgress:
    done: int = 0
    total: int = 0
    current_image: str | None = None
    errors: int = 0
    review_count: int = 0
    retries: int = 0


@dataclass(slots=True)
class InferenceMetrics:
    activity: str | None = None
    tokens_per_second: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    item_elapsed_seconds: float | None = None
    elapsed_seconds: float | None = None
    eta_seconds: float | None = None
    vram_used_bytes: int | None = None
    ram_used_bytes: int | None = None
    context_used_tokens: int | None = None
    context_limit_tokens: int | None = None
    backend: str | None = None
    model: str | None = None
    resource_profile: str | None = None


@dataclass(slots=True)
class Run:
    run_id: str
    project_id: str
    status: RunStatus = RunStatus.PENDING
    stage: RunStage = RunStage.DISCOVERY
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    scope_plan: dict[str, Any] = field(default_factory=dict)
    recipe_snapshot: dict[str, Any] = field(default_factory=dict)
    model_snapshot: dict[str, Any] = field(default_factory=dict)
    effective_resource_configuration: dict[str, Any] = field(default_factory=dict)
    progress: RunProgress = field(default_factory=RunProgress)
    inference_metrics: InferenceMetrics = field(default_factory=InferenceMetrics)
    summary: dict[str, Any] = field(default_factory=dict)
    pause_requested: bool = False
    stop_requested: bool = False
    last_heartbeat: str | None = None
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Run":
        values = dict(data)
        values["status"] = RunStatus(values.get("status", RunStatus.PENDING))
        values["stage"] = RunStage(values.get("stage", RunStage.DISCOVERY))
        progress = values.get("progress")
        if isinstance(progress, dict):
            values["progress"] = RunProgress(**progress)
        metrics = values.get("inference_metrics")
        if isinstance(metrics, dict):
            values["inference_metrics"] = InferenceMetrics(**metrics)
        return cls(**values)
