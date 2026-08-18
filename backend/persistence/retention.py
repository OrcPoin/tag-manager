from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

from backend.domain.common import to_primitive, utc_now

from .atomic import atomic_write_json
from .projects import ProjectRepository


class EventRetention:
    """Count-based retention that always materializes a summary before deletion."""

    def __init__(self, full_runs: int = 50, detail_days: int = 90,
                 max_project_bytes: int = 500 * 1024 * 1024):
        if full_runs < 30:
            raise ValueError("full_runs cannot be below the safe minimum of 30")
        self.full_runs = full_runs
        self.detail_days = max(1, detail_days)
        self.max_project_bytes = max(1, max_project_bytes)

    def rotate(self, repository: ProjectRepository) -> list[str]:
        runs_dir = repository.sidecar_path / "runs"
        if not runs_dir.is_dir():
            return []
        snapshots = sorted(
            (path for path in runs_dir.glob("run-*.json") if not path.name.endswith(".summary.json")),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        removed: list[str] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.detail_days)
        total_bytes = sum(path.stat().st_size for path in runs_dir.glob("*.events.*.jsonl"))
        candidates: list[Path] = []
        for index, snapshot_path in enumerate(snapshots):
            modified = datetime.fromtimestamp(snapshot_path.stat().st_mtime, timezone.utc)
            if index >= self.full_runs or modified < cutoff or total_bytes > self.max_project_bytes:
                candidates.append(snapshot_path)
                total_bytes -= sum(path.stat().st_size for path in runs_dir.glob(
                    f"{snapshot_path.stem}.events.*.jsonl"
                ))
        for snapshot_path in candidates:
            run_id = snapshot_path.name[len("run-"):-len(".json")]
            run = repository.load_run(run_id)
            summary_path = runs_dir / f"run-{run_id}.summary.json"
            if not summary_path.is_file():
                atomic_write_json(summary_path, {
                    "schema_version": 1,
                    "run_id": run.run_id,
                    "project_id": run.project_id,
                    "status": run.status.value,
                    "stage": run.stage.value,
                    "created_at": run.created_at,
                    "finished_at": run.finished_at,
                    "progress": to_primitive(run.progress),
                    "summary": run.summary,
                    "compacted_at": utc_now(),
                })
            for event_path in runs_dir.glob(f"run-{run_id}.events.*.jsonl"):
                event_path.unlink()
            removed.append(run_id)
        return removed

    @staticmethod
    def usage(repository: ProjectRepository) -> dict[str, int]:
        runs_dir = repository.sidecar_path / "runs"
        logs = list(runs_dir.glob("*.events.*.jsonl")) if runs_dir.is_dir() else []
        return {
            "event_bytes": sum(path.stat().st_size for path in logs),
            "event_segments": len(logs),
            "run_snapshots": len([path for path in runs_dir.glob("run-*.json") if not path.name.endswith(".summary.json")]) if runs_dir.is_dir() else 0,
            "summaries": len(list(runs_dir.glob("run-*.summary.json"))) if runs_dir.is_dir() else 0,
        }
