from __future__ import annotations

from pathlib import Path

from backend.domain.project import Project
from backend.domain.run import Run
from backend.domain.review import ReviewItem
from backend.domain.recipe import RecipeVersion

from .atomic import atomic_write_json, read_json


class ProjectRepository:
    SIDECAR = ".tagmanager"

    def __init__(self, dataset_path: str | Path):
        self.dataset_path = Path(dataset_path).resolve()
        self.sidecar_path = self.dataset_path / self.SIDECAR
        self.project_path = self.sidecar_path / "project.json"

    def initialize(self) -> None:
        if not self.dataset_path.is_dir():
            raise NotADirectoryError(self.dataset_path)
        for name in ("runs", "recipes"):
            (self.sidecar_path / name).mkdir(parents=True, exist_ok=True)

    def save(self, project: Project) -> None:
        if Path(project.dataset_path).resolve() != self.dataset_path:
            raise ValueError("Project dataset path does not match repository")
        self.initialize()
        atomic_write_json(self.project_path, project)

    def load(self) -> Project:
        return Project.from_dict(read_json(self.project_path))

    def save_run(self, run: Run) -> None:
        self.initialize()
        atomic_write_json(self.sidecar_path / "runs" / f"run-{run.run_id}.json", run)

    def load_run(self, run_id: str) -> Run:
        return Run.from_dict(read_json(self.sidecar_path / "runs" / f"run-{run_id}.json"))

    def save_review_queue(self, items: list[ReviewItem]) -> None:
        self.initialize()
        atomic_write_json(self.sidecar_path / "review_queue.json", {
            "schema_version": 1,
            "items": items,
        })

    def load_review_queue(self) -> list[ReviewItem]:
        path = self.sidecar_path / "review_queue.json"
        if not path.is_file():
            return []
        data = read_json(path)
        return [ReviewItem.from_dict(item) for item in data.get("items", [])]

    def save_recipe(self, recipe: RecipeVersion) -> None:
        self.initialize()
        path = self.sidecar_path / "recipes" / f"{recipe.recipe_id}-v{recipe.version}.json"
        if path.is_file():
            existing = RecipeVersion.from_dict(read_json(path))
            if existing.status.value != "draft" and existing.content_hash != recipe.content_hash:
                raise ValueError("Immutable recipe version cannot be overwritten")
        recipe.refresh_content_hash()
        atomic_write_json(path, recipe)

    def load_recipe(self, recipe_id: str, version: int) -> RecipeVersion:
        path = self.sidecar_path / "recipes" / f"{recipe_id}-v{version}.json"
        return RecipeVersion.from_dict(read_json(path))

    def delete_recipe(self, recipe_id: str, version: int) -> None:
        path = self.sidecar_path / "recipes" / f"{recipe_id}-v{version}.json"
        path.unlink()

    def list_recipes(self) -> list[RecipeVersion]:
        recipes = []
        for path in sorted((self.sidecar_path / "recipes").glob("*-v*.json")):
            try:
                recipes.append(RecipeVersion.from_dict(read_json(path)))
            except (OSError, TypeError, ValueError):
                continue
        return sorted(recipes, key=lambda item: (item.goal.casefold(), -item.version))
