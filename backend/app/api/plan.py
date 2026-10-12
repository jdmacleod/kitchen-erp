"""Plan (17, Phase 4A): the current kitchen and the kitchen's shopping list.

Every route takes a session or a full-access token; no API token scope reaches
the list, which ``CurrentUser`` enforces. Kitchen-scoped routes take an explicit
``home_base_id`` and never read the current-kitchen setting (09): the list
without one is a 422. The mutations a phone may retry take ``Idempotency``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, Idempotency
from app.schemas.plan import (
    ArchiveIn,
    ArchiveOut,
    FromRecipeIn,
    FromRecipeOut,
    ItemCreate,
    ItemOut,
    ItemPatch,
    ListOut,
    ListsOut,
    ListStatus,
    SettingsIn,
    SettingsOut,
)
from app.services import shopping_lists, user_settings

router = APIRouter(tags=["plan"])

HomeBaseId = Annotated[uuid.UUID, Query(description="the kitchen; never inferred")]


# --- the current kitchen ----------------------------------------------------------------


@router.get("/me/settings", response_model=SettingsOut)
async def get_settings(user: CurrentUser, db: DbSession) -> SettingsOut:
    return await user_settings.get_settings(db, user)


@router.put("/me/settings", response_model=SettingsOut)
async def put_settings(payload: SettingsIn, user: CurrentUser, db: DbSession) -> SettingsOut:
    """Persist the current kitchen; a kitchen that does not exist is a 422."""
    return await user_settings.put_settings(db, user, payload)


# --- the list ------------------------------------------------------------------------------


@router.get("/plan/list", response_model=ListOut)
async def get_list(
    _: CurrentUser,
    db: DbSession,
    home_base_id: HomeBaseId,
    list_id: Annotated[uuid.UUID | None, Query(description="an archived list to read")] = None,
) -> ListOut:
    """The kitchen's open list, created on first read; or one of its lists by id."""
    if list_id is not None:
        lst = await shopping_lists.get_list(db, home_base_id, list_id)
    else:
        lst = await shopping_lists.open_list(db, home_base_id)
    return await shopping_lists.list_out(db, lst)


@router.get("/plan/lists", response_model=ListsOut)
async def get_lists(
    _: CurrentUser,
    db: DbSession,
    home_base_id: HomeBaseId,
    status_: Annotated[ListStatus | None, Query(alias="status")] = "archived",
) -> ListsOut:
    """The kitchen's lists, newest first; archived ones unless ``status`` says otherwise."""
    return ListsOut(items=await shopping_lists.list_lists(db, home_base_id, status_))


@router.post("/plan/list/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def add_item(
    payload: ItemCreate, user: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    lst = await shopping_lists.open_list(db, payload.home_base_id)
    item = await shopping_lists.add_item(db, lst, user, payload)
    return await guard.commit(201, shopping_lists.item_out(item).model_dump(mode="json"))


@router.post("/plan/list/items/from-recipe", response_model=FromRecipeOut)
async def add_from_recipe(
    payload: FromRecipeIn, user: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    """Add a recipe's lines, scaled; convertible amounts aggregate, the rest keep their unit."""
    if guard.replay is not None:
        return guard.replay
    lst = await shopping_lists.open_list(db, payload.home_base_id)
    out = await shopping_lists.add_from_recipe(db, lst, user, payload.recipe_id, payload.scale)
    return await guard.commit(200, out.model_dump(mode="json"))


@router.post("/plan/list/archive", response_model=ArchiveOut)
async def archive(
    payload: ArchiveIn, _: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    """ "Start a fresh list": archive the open list and open the next, carrying
    unchecked items forward unless ``carry_over`` is false."""
    if guard.replay is not None:
        return guard.replay
    lst = await shopping_lists.open_list(db, payload.home_base_id)
    out = await shopping_lists.archive_and_renew(db, lst, payload.carry_over)
    return await guard.commit(200, out.model_dump(mode="json"))


@router.patch("/plan/list/items/{item_id}", response_model=ItemOut)
async def patch_item(
    item_id: uuid.UUID, payload: ItemPatch, _: CurrentUser, db: DbSession
) -> ItemOut:
    """Edit amounts, note, deferrable or min_quality; a stale ``updated_at`` is a 409."""
    item = await shopping_lists.get_item(db, item_id)
    return shopping_lists.item_out(await shopping_lists.update_item(db, item, payload))


@router.delete("/plan/list/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: uuid.UUID, _: CurrentUser, db: DbSession) -> Response:
    await shopping_lists.remove_item(db, await shopping_lists.get_item(db, item_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/plan/list/items/{item_id}/claim", response_model=ItemOut)
async def claim_item(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ItemOut:
    """A soft claim: records who is getting it; anyone may still check it off."""
    item = await shopping_lists.get_item(db, item_id)
    return shopping_lists.item_out(await shopping_lists.claim(db, item, user))


@router.delete("/plan/list/items/{item_id}/claim", response_model=ItemOut)
async def unclaim_item(item_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ItemOut:
    item = await shopping_lists.get_item(db, item_id)
    return shopping_lists.item_out(await shopping_lists.unclaim(db, item))


@router.post("/plan/list/items/{item_id}/check", response_model=ItemOut)
async def check_item(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ItemOut:
    """Check off: sets who and when and clears the claim; a retried tap is a 200."""
    item = await shopping_lists.get_item(db, item_id)
    return shopping_lists.item_out(await shopping_lists.check(db, item, user))


@router.delete("/plan/list/items/{item_id}/check", response_model=ItemOut)
async def uncheck_item(item_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ItemOut:
    item = await shopping_lists.get_item(db, item_id)
    return shopping_lists.item_out(await shopping_lists.uncheck(db, item))
