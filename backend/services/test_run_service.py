from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Any
from uuid import uuid4

from backend.domain.common import utc_now
from backend.persistence import ProjectRepository, atomic_write_json, read_json

from .project_service import ProjectService
from .scanner import IMAGE_EXTENSIONS, IGNORED_DIRECTORIES
from .run_service import RunService
from .run_coordinator import RunCoordinator


TestExecutor = Callable[[list[str], dict[str, Any]], list[dict[str, Any]]]


class TestRunService:
    def __init__(self, projects: ProjectService, runs: RunService,
                 coordinator: RunCoordinator, executor: TestExecutor | None = None):
        self.projects = projects
        self.runs = runs
        self.coordinator = coordinator
        self.executor = executor

    def execute(self, project_id: str, recipe_snapshot: dict[str, Any],
                idempotency_key: str, model_snapshot: dict[str, Any] | None = None,
                effective_resource_configuration: dict[str, Any] | None = None) -> dict[str, Any]:
        project = self.projects.get_project(project_id)
        proposed_id = f"test-{uuid4().hex}"
        test_id = self.projects.index.remember_command(idempotency_key, "test-run.create", proposed_id)
        repository = ProjectRepository(project.dataset_path)
        result_path = repository.sidecar_path / "runs" / f"{test_id}.json"
        if result_path.is_file():
            return read_json(result_path)
        recursive = bool(project.settings.get("include_subfolders", False))
        samples = self._representative_samples(Path(project.dataset_path), recursive)
        result: dict[str, Any] = {
            "test_id": test_id,
            "project_id": project_id,
            "created_at": utc_now(),
            "sample_images": samples,
            "recipe_snapshot": recipe_snapshot,
            "status": "pending",
            "results": [],
        }
        repository.initialize()
        atomic_write_json(result_path, result)
        if not samples:
            result.update(status="blocked", blocker="В dataset нет поддерживаемых изображений")
        elif self.executor is not None:
            try:
                result["results"] = self.executor(samples, recipe_snapshot)
                result["status"] = "completed"
                result["finished_at"] = utc_now()
            except Exception as error:
                result.update(status="failed", error=str(error), finished_at=utc_now())
        else:
            run = self.runs.create(
                project_id, {"images": samples, "test_drive": True,
                             "include_subfolders": recursive}, recipe_snapshot,
                f"{idempotency_key}:run", model_snapshot,
                effective_resource_configuration,
            )
            self.coordinator.start(run.run_id, f"{idempotency_key}:start")
            result.update(status="running", run_id=run.run_id)
        atomic_write_json(result_path, result)
        return result

    @staticmethod
    def _representative_samples(root: Path, recursive: bool = True) -> list[str]:
        images: list[tuple[int, str]] = []
        for current, directories, files in os.walk(root):
            directories[:] = sorted(name for name in directories if name not in IGNORED_DIRECTORIES)
            if not recursive:
                directories[:] = []
            current_path = Path(current)
            for name in sorted(files):
                path = current_path / name
                if path.suffix.lower() in IMAGE_EXTENSIONS:
                    try:
                        images.append((path.stat().st_size, path.relative_to(root).as_posix()))
                    except OSError:
                        continue
        images.sort()
        if len(images) <= 3:
            return [relative for _, relative in images]
        indices = (0, len(images) // 2, len(images) - 1)
        return [images[index][1] for index in indices]
