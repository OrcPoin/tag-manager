from __future__ import annotations

from backend.domain import RecipeStatus, RecipeVersion
from backend.persistence import ProjectRepository
from core.presets import load_presets
from backend.domain.common import to_primitive

from .project_service import ProjectService


class RecipeLifecycleError(ValueError):
    pass


class RecipeService:
    def __init__(self, projects: ProjectService):
        self.projects = projects

    def save_draft(self, project_id: str, recipe: RecipeVersion) -> RecipeVersion:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        try:
            existing = repository.load_recipe(recipe.recipe_id, recipe.version)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            existing.assert_editable()
        recipe.status = RecipeStatus.DRAFT
        repository.save_recipe(recipe)
        return recipe

    def list(self, project_id: str) -> list[RecipeVersion]:
        project = self.projects.get_project(project_id)
        project_recipes = ProjectRepository(project.dataset_path).list_recipes()
        names = {recipe.goal for recipe in project_recipes}
        legacy = []
        for index, (name, prompts) in enumerate(load_presets().items(), start=1):
            if name in names:
                continue
            legacy.append(RecipeVersion(
                recipe_id=f"legacy-preset-{index}", version=1, goal=name,
                result_type="hybrid_caption", status=RecipeStatus.IMMUTABLE,
                prompt=str(prompts.get("user", "")),
                instructions=str(prompts.get("system", "")),
                generation_settings={"origin": "legacy_presets_json"},
            ))
        return [*project_recipes, *legacy]

    def mark_used(self, project_id: str, recipe_id: str, version: int) -> RecipeVersion:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        recipe = repository.load_recipe(recipe_id, version)
        recipe.status = RecipeStatus.IMMUTABLE
        repository.save_recipe(recipe)
        return recipe

    def set_active(self, project_id: str, recipe_id: str, version: int) -> RecipeVersion:
        project = self.projects.get_project(project_id)
        recipe = ProjectRepository(project.dataset_path).load_recipe(recipe_id, version)
        if recipe.status is RecipeStatus.ARCHIVED:
            raise RecipeLifecycleError("Archived recipe cannot be active")
        self.projects.update_project(project_id, {"active_recipe_id": recipe_id, "active_recipe_version": version})
        return recipe

    def clone(self, project_id: str, recipe_id: str, version: int, new_recipe_id: str, name: str) -> RecipeVersion:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        source = next((item for item in self.list(project_id)
                       if item.recipe_id == recipe_id and item.version == version), None)
        if source is None:
            raise FileNotFoundError(recipe_id)
        if any(item.recipe_id == new_recipe_id for item in repository.list_recipes()):
            raise RecipeLifecycleError("Recipe id already exists")
        clone = RecipeVersion(
            recipe_id=new_recipe_id, version=1, goal=name, result_type=source.result_type,
            origin_recipe_id=source.recipe_id, prompt=source.prompt, instructions=source.instructions,
            pipeline_stages=list(source.pipeline_stages), quality_policy=dict(source.quality_policy),
            generation_settings=dict(source.generation_settings),
            compatible_model_constraints=dict(source.compatible_model_constraints),
        )
        return self.save_draft(project_id, clone)

    def archive(self, project_id: str, recipe_id: str, version: int) -> RecipeVersion:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        recipe = repository.load_recipe(recipe_id, version)
        recipe.status = RecipeStatus.ARCHIVED
        repository.save_recipe(recipe)
        if project.active_recipe_id == recipe_id and project.active_recipe_version == version:
            self.projects.update_project(project_id, {"active_recipe_id": None, "active_recipe_version": None})
        return recipe

    def delete_draft(self, project_id: str, recipe_id: str, version: int) -> None:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        recipe = repository.load_recipe(recipe_id, version)
        if recipe.status is not RecipeStatus.DRAFT:
            raise RecipeLifecycleError("Only unused draft versions can be deleted")
        if project.active_recipe_id == recipe_id and project.active_recipe_version == version:
            raise RecipeLifecycleError("Select another active recipe before deleting this draft")
        repository.delete_recipe(recipe_id, version)

    def compare(self, project_id: str, recipe_id: str, left_version: int, right_version: int) -> dict:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        left = repository.load_recipe(recipe_id, left_version)
        right = repository.load_recipe(recipe_id, right_version)
        return {
            "recipe_id": recipe_id, "left_version": left_version, "right_version": right_version,
            "identical": to_primitive(left) == to_primitive(right),
            "differences": self._diff(to_primitive(left), to_primitive(right)),
        }

    @classmethod
    def _diff(cls, left: dict, right: dict, prefix: str = "") -> list[dict]:
        result = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            a, b = left.get(key), right.get(key)
            if isinstance(a, dict) and isinstance(b, dict):
                result.extend(cls._diff(a, b, path))
            elif a != b:
                result.append({"path": path, "left": a, "right": b})
        return result
