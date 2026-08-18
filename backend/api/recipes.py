from __future__ import annotations

import re
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.domain import RecipeVersion
from backend.domain.common import to_primitive
from backend.services import RecipeService
from backend.services.recipe_service import RecipeLifecycleError
from backend.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/api/projects/{project_id}/recipes", tags=["recipes"])


class SaveRecipeRequest(BaseModel):
    recipe_id: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    result_type: str = "hybrid_caption"
    system_prompt: str = Field(default="", max_length=30000)
    user_prompt: str = Field(default="", max_length=30000)
    settings: dict = Field(default_factory=dict)


class CloneRecipeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    recipe_id: str | None = Field(default=None, max_length=100)


def service(request: Request) -> RecipeService:
    return request.app.state.recipe_service


@router.get("")
def list_recipes(project_id: str, recipes: RecipeService = Depends(service)):
    try:
        return [to_primitive(item) for item in recipes.list(project_id)]
    except ProjectNotFoundError as error:
        raise HTTPException(404, detail={"message": "Проект не найден"}) from error


@router.post("")
def save_recipe(project_id: str, body: SaveRecipeRequest, recipes: RecipeService = Depends(service)):
    recipe_id = body.recipe_id or re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "recipe"
    existing = recipes.list(project_id)
    versions = [item.version for item in existing if item.recipe_id == recipe_id]
    version = max(versions, default=0) + 1
    recipe = RecipeVersion(
        recipe_id=recipe_id, version=version, goal=body.name,
        result_type=body.result_type, prompt=body.user_prompt,
        instructions=body.system_prompt, generation_settings=body.settings,
    )
    return to_primitive(recipes.save_draft(project_id, recipe))


@router.get("/compare/{recipe_id}/{left_version}/{right_version}")
def compare_recipe(project_id: str, recipe_id: str, left_version: int, right_version: int,
                   recipes: RecipeService = Depends(service)):
    try:
        return recipes.compare(project_id, recipe_id, left_version, right_version)
    except FileNotFoundError as error:
        raise HTTPException(404, detail={"code": "recipe_not_found", "message": "Recipe version not found"}) from error


@router.post("/{recipe_id}/{version}/active")
def activate_recipe(project_id: str, recipe_id: str, version: int, recipes: RecipeService = Depends(service)):
    try:
        return to_primitive(recipes.set_active(project_id, recipe_id, version))
    except FileNotFoundError as error:
        raise HTTPException(404, detail={"code": "recipe_not_found", "message": "Recipe version not found"}) from error
    except RecipeLifecycleError as error:
        raise HTTPException(409, detail={"code": "recipe_activation_rejected", "message": str(error)}) from error


@router.post("/{recipe_id}/{version}/clone")
def clone_recipe(project_id: str, recipe_id: str, version: int, body: CloneRecipeRequest,
                 recipes: RecipeService = Depends(service)):
    new_id = body.recipe_id or re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "recipe-copy"
    try:
        return to_primitive(recipes.clone(project_id, recipe_id, version, new_id, body.name))
    except FileNotFoundError as error:
        raise HTTPException(404, detail={"code": "recipe_not_found", "message": "Recipe version not found"}) from error
    except RecipeLifecycleError as error:
        raise HTTPException(409, detail={"code": "recipe_clone_rejected", "message": str(error)}) from error


@router.post("/{recipe_id}/{version}/archive")
def archive_recipe(project_id: str, recipe_id: str, version: int, recipes: RecipeService = Depends(service)):
    try:
        return to_primitive(recipes.archive(project_id, recipe_id, version))
    except FileNotFoundError as error:
        raise HTTPException(404, detail={"code": "recipe_not_found", "message": "Recipe version not found"}) from error


@router.delete("/{recipe_id}/{version}")
def delete_recipe(project_id: str, recipe_id: str, version: int, recipes: RecipeService = Depends(service)):
    try:
        recipes.delete_draft(project_id, recipe_id, version)
        return {"deleted": True, "recipe_id": recipe_id, "version": version}
    except FileNotFoundError as error:
        raise HTTPException(404, detail={"code": "recipe_not_found", "message": "Recipe version not found"}) from error
    except RecipeLifecycleError as error:
        raise HTTPException(409, detail={"code": "recipe_delete_rejected", "message": str(error)}) from error
