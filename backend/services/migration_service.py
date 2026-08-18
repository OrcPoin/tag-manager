from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from backend.domain import RecipeVersion
from backend.domain.common import utc_now
from backend.persistence import ProjectRepository, atomic_write_json

from .project_service import ProjectService
from .recipe_service import RecipeService


class MigrationService:
    def __init__(self, projects: ProjectService):
        self.projects = projects
        self.recipes = RecipeService(projects)

    def import_legacy(self, project_id: str, presets_path: str | Path | None = None) -> dict[str, Any]:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        repository.initialize()
        report: dict[str, Any] = {
            "schema_version": 1,
            "project_id": project_id,
            "migrated_at": utc_now(),
            "imported": {"presets": 0, "progress": False, "review": False},
            "warnings": [],
        }
        backup_dir = repository.sidecar_path / "migration-backup"

        progress_path = Path(project.dataset_path) / "progress.json"
        if progress_path.is_file():
            data = self._read_object(progress_path, report)
            if data is not None:
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(progress_path, backup_dir / "progress.json")
                atomic_write_json(repository.sidecar_path / "legacy_progress.json", data)
                report["imported"]["progress"] = True

        review_path = Path(project.dataset_path) / ".tagmanager_review.json"
        if review_path.is_file():
            data = self._read_object(review_path, report)
            if data is not None:
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(review_path, backup_dir / ".tagmanager_review.json")
                atomic_write_json(repository.sidecar_path / "legacy_review.json", data)
                report["imported"]["review"] = True

        if presets_path is not None and Path(presets_path).is_file():
            presets = self._read_object(Path(presets_path), report)
            if presets is not None:
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(presets_path, backup_dir / "presets.json")
                for index, (name, values) in enumerate(presets.items(), start=1):
                    if not isinstance(values, dict):
                        report["warnings"].append(f"Preset {name!r} пропущен: неверный формат")
                        continue
                    recipe = RecipeVersion(
                        recipe_id=f"legacy-{index}", version=1, goal=str(name),
                        result_type="hybrid_caption", prompt=str(values.get("user", "")),
                        instructions=str(values.get("system", "")),
                    )
                    self.recipes.save_draft(project_id, recipe)
                    report["imported"]["presets"] += 1

        atomic_write_json(repository.sidecar_path / "migration.json", report)
        return report

    @staticmethod
    def _read_object(path: Path, report: dict[str, Any]) -> dict[str, Any] | None:
        try:
            with path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError("root is not an object")
            return data
        except (OSError, ValueError) as error:
            report["warnings"].append(f"{path.name}: {error}")
            return None
