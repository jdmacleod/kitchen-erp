"""Recipes (07, Phase 3): the indexed repository, its status, rescans and relinks (3A).

Every route takes a session or a full-access token; no API token scope reaches
recipes (07, "Data model additions"), which ``CurrentUser`` enforces.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.recipes import (
    RecipeList,
    RecipeOut,
    RecipesStatus,
    RecipeStatus,
    RelinkIn,
    ScanOut,
)
from app.services import recipes

router = APIRouter(tags=["recipes"])


@router.get("/recipes", response_model=RecipeList)
async def list_recipes(
    _: CurrentUser,
    db: DbSession,
    status_: Annotated[RecipeStatus | None, Query(alias="status")] = None,
    dirty: bool | None = None,
) -> RecipeList:
    return RecipeList(items=await recipes.list_recipes(db, status=status_, dirty=dirty))


@router.get("/recipes/status", response_model=RecipesStatus)
async def recipes_status(_: CurrentUser, db: DbSession) -> RecipesStatus:
    return await recipes.status(db)


@router.post("/recipes/rescan", response_model=ScanOut)
async def rescan(_: CurrentUser, db: DbSession) -> ScanOut:
    """Run the scan inline and answer when it is done."""
    return await recipes.scan(db)


@router.get("/recipes/{recipe_id}", response_model=RecipeOut)
async def get_recipe(recipe_id: uuid.UUID, _: CurrentUser, db: DbSession) -> RecipeOut:
    return await recipes.recipe_out(db, await recipes.get_recipe(db, recipe_id))


@router.post("/recipes/{recipe_id}/relink", response_model=RecipeOut)
async def relink(
    recipe_id: uuid.UUID, payload: RelinkIn, _: CurrentUser, db: DbSession
) -> RecipeOut:
    row = await recipes.relink(db, recipe_id, payload.target_id)
    return await recipes.recipe_out(db, row)


@router.delete("/recipes/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(recipe_id: uuid.UUID, _: CurrentUser, db: DbSession) -> Response:
    await recipes.delete_recipe(db, recipe_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
