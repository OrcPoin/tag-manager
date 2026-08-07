"""Backend-independent result models shared by VLM and future taggers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ScoredTag:
    name: str
    confidence: float
    category: str = "general"

    def __post_init__(self) -> None:
        clean = self.name.strip()
        if not clean:
            raise ValueError("Tag name cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Tag confidence must be in range 0..1")
        object.__setattr__(self, "name", clean)


@dataclass
class TaggerResult:
    success: bool
    general: list[ScoredTag] = field(default_factory=list)
    characters: list[ScoredTag] = field(default_factory=list)
    rating: list[ScoredTag] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    error: str = ""

    @property
    def tags(self) -> list[ScoredTag]:
        return [*self.general, *self.characters, *self.rating]


@dataclass
class VLMResult:
    success: bool
    text: str = ""
    error: str = ""
    attempts: int = 0
    prompt_tokens: int | None = None
    generated_tokens: int | None = None
    elapsed_seconds: float | None = None


@dataclass
class RunProvenance:
    run_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    backend: str = ""
    backend_version: str = ""
    vlm_model: str = ""
    vlm_model_hash: str = ""
    taggers: list[dict[str, object]] = field(default_factory=list)
    prompt_hash: str = ""
    generation_settings: dict[str, object] = field(default_factory=dict)
    preprocessing: dict[str, object] = field(default_factory=dict)
    hardware: dict[str, object] = field(default_factory=dict)


@dataclass
class PipelineResult:
    success: bool
    final_caption: str = ""
    tagger_results: list[TaggerResult] = field(default_factory=list)
    vlm_result: VLMResult | None = None
    provenance: RunProvenance | None = None
    warnings: list[str] = field(default_factory=list)
    error: str = ""
