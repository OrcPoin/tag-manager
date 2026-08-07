"""Policies and modes for the independent tagger/VLM stages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PipelineMode(str, Enum):
    TAGS_ONLY = "tags_only"
    DESCRIPTION_ONLY = "description_only"
    TAGS_AND_DESCRIPTION = "tags_and_description"
    TAGS_TO_VLM_CONTEXT = "tags_to_vlm_context"
    CUSTOM = "custom"


class FailureAction(str, Enum):
    STOP = "stop"
    SKIP = "skip"
    CONTINUE = "continue"


@dataclass(frozen=True)
class StagePolicy:
    retries: int = 1
    timeout_seconds: float = 120.0
    failure_action: FailureAction = FailureAction.CONTINUE


@dataclass(frozen=True)
class PipelinePolicy:
    tagger: StagePolicy = StagePolicy(retries=1, timeout_seconds=120.0)
    vlm: StagePolicy = StagePolicy(retries=1, timeout_seconds=900.0, failure_action=FailureAction.STOP)
    merge_strategy: str = "max_confidence"
