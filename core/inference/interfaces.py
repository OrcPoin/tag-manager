"""Stable contracts between the application and an inference implementation.

The worker and UI must depend on these contracts instead of llama.cpp CLI flags
or a concrete HTTP SDK.  The current external OpenAI-compatible client already
implements the protocol structurally; the managed llama.cpp backend will do the
same in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, runtime_checkable


class BackendStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True)
class BackendHealth:
    status: BackendStatus
    message: str = ""
    model: str | None = None
    version: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status in (BackendStatus.READY, BackendStatus.BUSY)


@dataclass(frozen=True)
class ModelDescriptor:
    model_id: str
    path: str = ""
    display_name: str = ""
    architecture: str = ""
    quantization: str = ""
    multimodal: bool = False
    mmproj_path: str = ""
    sha256: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id cannot be empty")


@dataclass(frozen=True)
class GenerationRequest:
    image_path: str
    system_prompt: str
    user_prompt: str
    temperature: float
    max_tokens: int
    top_p: float
    disable_thinking: bool = False


@dataclass(frozen=True)
class GenerationResponse:
    success: bool
    text: str = ""
    error: str = ""
    attempts: int = 0
    quality_reason: str = ""
    stopped: bool = False
    prompt_tokens: int | None = None
    generated_tokens: int | None = None
    elapsed_seconds: float | None = None


@runtime_checkable
class InferenceBackend(Protocol):
    """Minimum interface consumed by captioning application logic."""

    model: str

    def start(self): ...

    def stop(self) -> bool: ...

    def restart(self): ...

    def check_connection(self) -> tuple[bool, str]: ...

    def list_models(self) -> list[str]: ...

    def active_model(self) -> str | None: ...

    def health(self) -> BackendHealth: ...

    def stop_generation(self) -> bool: ...

    def generate_caption(
        self,
        image_path: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        top_p: float,
        on_attempt: Callable[[int, str], None] | None = None,
        max_caption_retries: int | None = None,
        should_stop: Callable[[], bool] | None = None,
        disable_thinking: bool = False,
    ): ...
