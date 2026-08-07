"""Dataset run IDs and atomic, redacted run snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import tempfile
import uuid
from typing import Any


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    folder: str
    started_at: str
    backend: str
    model: str
    params: dict[str, object]
    hardware: dict[str, object] | None = None
    status: str = "running"
    finished_at: str = ""
    summary: dict[str, object] | None = None


def new_run_snapshot(
    folder: str,
    params: dict,
    backend,
    *,
    hardware: dict[str, object] | None = None,
) -> RunSnapshot:
    backend_name = getattr(backend, "backend_name", backend.__class__.__name__)
    model = str(getattr(backend, "model", ""))
    safe_params = {
        str(key): value for key, value in params.items()
        if isinstance(value, (str, int, float, bool, type(None)))
        and "key" not in str(key).lower()
    }
    return RunSnapshot(
        uuid.uuid4().hex,
        os.path.abspath(folder),
        datetime.now(timezone.utc).isoformat(),
        backend_name, model, safe_params, hardware,
    )


def write_run_snapshot(folder: str, snapshot: RunSnapshot) -> str:
    sidecar = os.path.join(folder, ".tagmanager")
    os.makedirs(sidecar, exist_ok=True)
    path = os.path.join(sidecar, f"run-{snapshot.run_id}.json")
    payload = {
        "schema_version": 1,
        "run_id": snapshot.run_id,
        "folder": snapshot.folder,
        "started_at": snapshot.started_at,
        "finished_at": snapshot.finished_at,
        "status": snapshot.status,
        "backend": snapshot.backend,
        "model": snapshot.model,
        "params": snapshot.params,
        "hardware": snapshot.hardware or {},
        "summary": snapshot.summary or {},
    }
    fd, temp_path = tempfile.mkstemp(prefix=".run-", dir=sidecar, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return path


def finish_run_snapshot(
    snapshot: RunSnapshot, status: str, summary: dict[str, object] | None = None,
) -> RunSnapshot:
    return RunSnapshot(
        snapshot.run_id, snapshot.folder, snapshot.started_at,
        snapshot.backend, snapshot.model, snapshot.params,
        snapshot.hardware, status,
        datetime.now(timezone.utc).isoformat(), summary or snapshot.summary,
    )


def list_run_snapshots(folder: str, limit: int = 50) -> list[dict[str, Any]]:
    """Прочитать историю Run безопасно, пропуская повреждённые sidecar-файлы."""
    sidecar = os.path.join(os.path.abspath(folder), ".tagmanager")
    if not os.path.isdir(sidecar):
        return []
    rows: list[dict[str, Any]] = []
    for name in os.listdir(sidecar):
        if not name.startswith("run-") or not name.endswith(".json"):
            continue
        path = os.path.join(sidecar, name)
        try:
            with open(path, encoding="utf-8") as stream:
                payload = json.load(stream)
            if isinstance(payload, dict) and payload.get("run_id"):
                payload["_path"] = path
                rows.append(payload)
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda row: str(row.get("started_at", "")), reverse=True)
    return rows[:max(0, limit)]
