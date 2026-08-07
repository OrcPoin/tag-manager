"""Inference backends and stable application-facing contracts."""

from core.inference.interfaces import (
    BackendHealth,
    BackendStatus,
    GenerationRequest,
    GenerationResponse,
    InferenceBackend,
    ModelDescriptor,
)

__all__ = [
    "BackendHealth", "BackendStatus", "GenerationRequest", "GenerationResponse",
    "InferenceBackend", "ModelDescriptor",
]
