"""Redacted diagnostics export for support and reproducibility."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def _redact(value: Any):
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if any(token in key.lower() for token in ("key", "token", "secret"))
                  else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def export_diagnostics(
    path: str,
    *,
    worker_snapshot: dict,
    backend_health=None,
    profile=None,
    compatibility=None,
) -> str:
    payload = {
        "schema_version": 1,
        "worker": worker_snapshot,
        "backend": backend_health.__dict__ if backend_health else {},
        "profile": profile.__dict__ if profile else {},
        "compatibility": {
            "severity": compatibility.severity,
            "summary": compatibility.summary,
            "budget": compatibility.budget.__dict__,
            "recommendations": compatibility.recommendations,
        } if compatibility else {},
    }
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".diagnostics-", dir=folder, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(_redact(payload), stream, ensure_ascii=False, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return path
