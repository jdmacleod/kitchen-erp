"""Plan (17, Phase 4A): the current kitchen and the kitchen's shopping list.

Every route takes a session or a full-access token; no API token scope reaches
the list, which ``CurrentUser`` enforces. Kitchen-scoped routes take an explicit
``home_base_id`` and never read the current-kitchen setting (09): the list
without one is a 422. The mutations a phone may retry take ``Idempotency``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentAdmin, CurrentUser, DbSession, Idempotency
from app.core import config
from app.schemas.plan import (
    ArchiveIn,
    ArchiveOut,
    ChooseIn,
    FromRecipeIn,
    FromRecipeOut,
    GetHereIn,
    ItemCreate,
    ItemOut,
    ItemPatch,
    ListOut,
    ListsOut,
    ListStatus,
    PlanDefaultsOut,
    RecomputeLegsIn,
    RecomputeLegsOut,
    SettingsIn,
    SettingsOut,
    StopDoneIn,
    StopDoneOut,
    StopPriceIn,
    StopScreenOut,
    TravelLegOut,
    TripCreate,
    TripOut,
    TripsOut,
)
from app.services import shopping_lists, travel, trip_planner, trip_stops, user_settings

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
    out = await shopping_lists.add_from_recipe(
        db, lst, user, payload.recipe_id, payload.scale, payload.replace
    )
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


# --- drive times (17, 4B) -----------------------------------------------------------------


@router.get("/geo/travel-legs", response_model=TravelLegOut)
async def get_travel_leg(
    _: CurrentUser,
    db: DbSession,
    from_: Annotated[uuid.UUID, Query(alias="from", description="the origin place")],
    to: Annotated[uuid.UUID, Query(description="the destination place")],
) -> TravelLegOut:
    """One free-flow leg, computed if missing: from the engine when it answers,
    otherwise an estimate marked so."""
    leg = await travel.leg_between(db, from_, to, config.get_settings())
    return TravelLegOut.model_validate(leg)


@router.post("/geo/travel-legs/recompute", response_model=RecomputeLegsOut)
async def recompute_travel_legs(
    payload: RecomputeLegsIn, _: CurrentAdmin, db: DbSession
) -> RecomputeLegsOut:
    """Drop estimated legs (every leg with ``only_estimates`` false) and compute
    them again; what the engine does not answer is estimated."""
    counts = await travel.recompute_legs(
        db, config.get_settings(), only_estimates=payload.only_estimates
    )
    return RecomputeLegsOut(dropped=counts.dropped, osrm=counts.osrm, estimated=counts.estimated)


# --- trip plans (17, 4B) --------------------------------------------------------------------


@router.get("/plan/defaults", response_model=PlanDefaultsOut)
async def plan_defaults(_: CurrentUser) -> PlanDefaultsOut:
    """What a new plan starts from when the request leaves a field out."""
    settings = config.get_settings()
    return PlanDefaultsOut(
        value_of_time_per_hour=settings.plan_value_of_time_per_hour,
        max_stops_default=TripCreate.model_fields["max_stops"].default,
        candidate_radius_km=settings.plan_candidate_radius_km,
    )


@router.post("/plan/trips", response_model=TripOut, status_code=status.HTTP_201_CREATED)
async def create_trip(
    payload: TripCreate, user: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    """Plan a trip from the kitchen's open list: the three variants, their stops
    and what to buy at each. An empty list is 422 ``empty_list``."""
    if guard.replay is not None:
        return guard.replay
    plan = await trip_planner.create_plan(db, user, payload, config.get_settings())
    return await guard.commit(201, trip_planner.trip_out(plan).model_dump(mode="json"))


@router.get("/plan/trips", response_model=TripsOut)
async def list_trips(_: CurrentUser, db: DbSession, home_base_id: HomeBaseId) -> TripsOut:
    """The kitchen's plans, newest first, with each variant's totals."""
    plans = await trip_planner.list_plans(db, home_base_id)
    return TripsOut(items=[trip_planner.trip_summary(p) for p in plans])


@router.get("/plan/trips/{plan_id}", response_model=TripOut)
async def get_trip(plan_id: uuid.UUID, _: CurrentUser, db: DbSession) -> TripOut:
    return trip_planner.trip_out(await trip_planner.get_plan(db, plan_id))


@router.post("/plan/trips/{plan_id}/choose", response_model=TripOut)
async def choose_variant(
    plan_id: uuid.UUID, payload: ChooseIn, _: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    """Freeze one variant as the prediction; a second choice is 409 ``already_chosen``."""
    if guard.replay is not None:
        return guard.replay
    plan = await trip_planner.get_plan(db, plan_id)
    plan = await trip_planner.choose(db, plan, payload.variant_id)
    return await guard.commit(200, trip_planner.trip_out(plan).model_dump(mode="json"))


@router.post("/plan/trips/{plan_id}/abandon", response_model=TripOut)
async def abandon_trip(plan_id: uuid.UUID, _: CurrentUser, db: DbSession) -> TripOut:
    """End a planned or underway plan."""
    plan = await trip_planner.get_plan(db, plan_id)
    return trip_planner.trip_out(await trip_planner.abandon(db, plan))


@router.delete("/plan/trips/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(plan_id: uuid.UUID, _: CurrentUser, db: DbSession) -> Response:
    """Remove an unchosen plan; a chosen one is 409 ``plan_chosen``."""
    plan = await trip_planner.get_plan(db, plan_id)
    await trip_planner.delete_plan(db, plan)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- at the store (17, 4C) ------------------------------------------------------------------

StopSeq = Annotated[int, Path(ge=1, le=32)]
STOP_PRICE = "/plan/trips/{plan_id}/stops/{seq}/items/{item_id}/price"


@router.get("/plan/trips/{plan_id}/stops/{seq}", response_model=StopScreenOut)
async def get_stop(
    plan_id: uuid.UUID, seq: StopSeq, _: CurrentUser, db: DbSession
) -> StopScreenOut:
    """One stop of the chosen variant: what to get here, what a later stop's
    items would cost here ("Cheaper at stop {m}"), and the cost here. A plan
    with no chosen variant is 409 ``plan_not_chosen``."""
    plan = await trip_planner.get_plan(db, plan_id)
    return await trip_stops.stop_screen(db, plan, seq, config.get_settings())


@router.put(STOP_PRICE, response_model=StopScreenOut)
async def edit_stop_price(
    plan_id: uuid.UUID,
    seq: StopSeq,
    item_id: uuid.UUID,
    payload: StopPriceIn,
    _: CurrentUser,
    db: DbSession,
) -> StopScreenOut:
    """Hold a price read in the aisle on the stop item; it records nothing until
    "Done here". A done stop is 409 ``stop_done``."""
    plan = await trip_planner.get_plan(db, plan_id)
    return await trip_stops.edit_price(db, plan, seq, item_id, payload, config.get_settings())


@router.delete(STOP_PRICE, response_model=StopScreenOut)
async def clear_stop_price(
    plan_id: uuid.UUID, seq: StopSeq, item_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> StopScreenOut:
    """Drop an edited price; the item goes back to the plan's price."""
    plan = await trip_planner.get_plan(db, plan_id)
    return await trip_stops.edit_price(db, plan, seq, item_id, None, config.get_settings())


@router.post("/plan/trips/{plan_id}/stops/{seq}/get-here", response_model=StopScreenOut)
async def get_here(
    plan_id: uuid.UUID,
    seq: StopSeq,
    payload: GetHereIn,
    _: CurrentUser,
    db: DbSession,
    guard: Idempotency,
) -> JSONResponse:
    """Move a later stop's item here, re-priced at this location's offer or the
    price given; with neither it is 422 ``not_offered_here``."""
    if guard.replay is not None:
        return guard.replay
    plan = await trip_planner.get_plan(db, plan_id)
    out = await trip_stops.get_here(db, plan, seq, payload, config.get_settings())
    return await guard.commit(200, out.model_dump(mode="json"))


@router.post("/plan/trips/{plan_id}/stops/{seq}/done", response_model=StopDoneOut)
async def stop_done(
    plan_id: uuid.UUID,
    seq: StopSeq,
    payload: StopDoneIn,
    user: CurrentUser,
    db: DbSession,
    guard: Idempotency,
) -> JSONResponse:
    """ "Done here" (S1), in one transaction: one committed manual purchase for the
    checked items, linked to the plan, its lines at the planned price flagged
    ``planned_price``; a shelf observation for each price edited but not bought;
    the list items checked; the stop done and the plan moved on. The same key
    replays; a stop already done is 409 ``stop_done``, an unchosen plan 409
    ``plan_not_chosen``."""
    if guard.replay is not None:
        return guard.replay
    await trip_stops.lock_plan(db, plan_id)
    plan = await trip_planner.get_plan(db, plan_id)
    out = await trip_stops.done(db, user, plan, seq, payload)
    if guard.key is None:
        await db.commit()
    return await guard.commit(200, out.model_dump(mode="json"))
