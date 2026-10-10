"""Recipes (07, Phase 3): the indexed repository, its status, rescans and relinks
(3A); the resolve queue, decisions and pins (3C); cost snapshots (3D).

Every route takes a session or a full-access token; no API token scope reaches
recipes (07, "Data model additions"), which ``CurrentUser`` enforces.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.recipes import (
    Completeness,
    CostBasis,
    CostHistoryOut,
    RecipeCostOut,
    RecipeList,
    RecipeOut,
    RecipePinIn,
    RecipesStatus,
    RecipeStatus,
    RelinkIn,
    ResolveAskIn,
    ResolveAskOut,
    ResolveDecisionIn,
    ResolveDecisionOut,
    ResolveQueueOut,
    ScanOut,
)
from app.services import recipe_cost_views, recipe_costing, recipe_resolution, recipes

router = APIRouter(tags=["recipes"])


@router.get("/recipes", response_model=RecipeList)
async def list_recipes(
    _: CurrentUser,
    db: DbSession,
    status_: Annotated[RecipeStatus | None, Query(alias="status")] = None,
    dirty: bool | None = None,
    q: Annotated[str | None, Query(max_length=200, description="title or path")] = None,
    completeness: Completeness | None = None,
) -> RecipeList:
    """Ordered by title; every filter runs here, not in the browser (UI-7.3)."""
    return RecipeList(
        items=await recipes.list_recipes(
            db, status=status_, dirty=dirty, q=q, completeness=completeness
        )
    )


@router.get("/recipes/status", response_model=RecipesStatus)
async def recipes_status(_: CurrentUser, db: DbSession) -> RecipesStatus:
    return await recipes.status(db)


@router.post("/recipes/rescan", response_model=ScanOut)
async def rescan(_: CurrentUser, db: DbSession) -> ScanOut:
    """Run the scan inline and answer when it is done."""
    return await recipes.scan(db)


@router.get("/recipes/resolve", response_model=ResolveQueueOut)
async def resolve_queue(_: CurrentUser, db: DbSession) -> ResolveQueueOut:
    """Unmatched names grouped by normalized name, most-used first, with proposals."""
    return await recipe_resolution.queue(db)


@router.post("/recipes/resolve", response_model=ResolveDecisionOut)
async def resolve_decide(
    payload: ResolveDecisionIn, user: CurrentUser, db: DbSession
) -> ResolveDecisionOut:
    """One decision for a name: an ingredient, a new one, or not an ingredient."""
    return await recipe_resolution.decide(db, user, payload)


@router.post("/recipes/resolve/ask", response_model=ResolveAskOut)
async def resolve_ask(payload: ResolveAskIn, _: CurrentUser, db: DbSession) -> ResolveAskOut:
    """The cascade for one queued name, the local model included; suggestions only."""
    return await recipe_resolution.ask(db, payload.name_norm)


@router.get("/recipes/{recipe_id}", response_model=RecipeOut)
async def get_recipe(recipe_id: uuid.UUID, _: CurrentUser, db: DbSession) -> RecipeOut:
    return await recipes.recipe_out(db, await recipes.get_recipe(db, recipe_id))


@router.get("/recipes/{recipe_id}/cost", response_model=RecipeCostOut)
async def get_cost(
    recipe_id: uuid.UUID,
    _: CurrentUser,
    db: DbSession,
    basis: CostBasis = "latest",
    window_days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    min_quality: Annotated[int | None, Query(ge=1, le=5)] = None,
) -> RecipeCostOut:
    """The recipe's cost under a basis, computed now when its current content has none.

    ``window_days`` applies to ``average`` only and defaults to the stale window.
    """
    recipe = await recipes.get_recipe(db, recipe_id)
    snapshot = await recipe_costing.find_snapshot(db, recipe, basis, window_days, min_quality)
    if snapshot is None:
        snapshot = await recipe_costing.compute_snapshot(
            db, recipe, basis, window_days, min_quality
        )
    return await recipe_cost_views.cost_out(db, snapshot)


@router.get("/recipes/{recipe_id}/cost/history", response_model=CostHistoryOut)
async def get_cost_history(
    recipe_id: uuid.UUID, _: CurrentUser, db: DbSession, basis: CostBasis = "latest"
) -> CostHistoryOut:
    """Committed (non-provisional) snapshots under a basis, oldest first."""
    recipe = await recipes.get_recipe(db, recipe_id)
    return await recipe_cost_views.history_out(db, recipe, basis)


@router.post("/recipes/{recipe_id}/relink", response_model=RecipeOut)
async def relink(
    recipe_id: uuid.UUID, payload: RelinkIn, _: CurrentUser, db: DbSession
) -> RecipeOut:
    row = await recipes.relink(db, recipe_id, payload.target_id)
    return await recipes.recipe_out(db, row)


@router.put("/recipes/{recipe_id}/pins/{name_norm}", response_model=RecipeOut)
async def set_pin(
    recipe_id: uuid.UUID, name_norm: str, payload: RecipePinIn, _: CurrentUser, db: DbSession
) -> RecipeOut:
    row = await recipe_resolution.set_pin(db, recipe_id, name_norm, payload.product_id)
    return await recipes.recipe_out(db, row)


@router.delete("/recipes/{recipe_id}/pins/{name_norm}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pin(
    recipe_id: uuid.UUID, name_norm: str, _: CurrentUser, db: DbSession
) -> Response:
    await recipe_resolution.delete_pin(db, recipe_id, name_norm)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/recipes/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(recipe_id: uuid.UUID, _: CurrentUser, db: DbSession) -> Response:
    await recipes.delete_recipe(db, recipe_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
