"""At the store (17, 4C): one stop of a chosen plan, a price edited in the aisle,
"Get here", and "Done here".

A stop screen reads the chosen variant's stop: the items planned here with the
price the plan rests on, and the items assigned to later stops that this
location could also fill, priced here by ``trip_offers`` as the planner would
have priced them. A price edited in the aisle is held on the stop item
(``actual_price`` for ``actual_qty actual_unit`` of its product) and records
nothing by itself; "Get here" moves a later stop's item to this stop
(``got_here``), re-priced at this location's offer or an edited price.

"Done here" is one transaction (ruling S1): one committed manual purchase at
this location, linked to the plan, with a line per checked item at its edited
price or its planned pack price times packs, through the manual-purchase path
that emits one observation per line; a line at the planned price carries the
flag ``planned_price``; a price edited but not bought becomes one shelf
observation; the checked list items are checked and their claims cleared; the
stop records ``done_at`` and its purchase; the plan moves to ``underway`` on
the first stop and to ``done`` after the last. Nothing archives the list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import ApiError
from app.models import AppUser, Ingredient, PriceObservation, Product
from app.models.geo import HomeBase
from app.models.plan import (
    ShoppingListItem,
    TripPlan,
    TripPlanStop,
    TripPlanStopItem,
    TripPlanVariant,
)
from app.schemas.plan import (
    AmountOut,
    CheaperElsewhereItem,
    GetHereIn,
    ProductBrief,
    StopDoneIn,
    StopDoneOut,
    StopLocation,
    StopPriceIn,
    StopScreenItem,
    StopScreenOut,
    UserBrief,
)
from app.schemas.purchases import LineIn, ManualPurchaseIn
from app.services import pricebook, purchases, trip_offers, trip_planner
from app.services.packs import CTX, money
from app.services.units import load_units

OPEN_STATUSES = ("planned", "underway")


# --- lookups ---------------------------------------------------------------------------------


def _chosen(plan_row: TripPlan) -> TripPlanVariant:
    variant = trip_planner.chosen_variant(plan_row)
    if variant is None:
        raise ApiError(409, "plan_not_chosen", "Choose a plan first.")
    return variant


def _stop(variant: TripPlanVariant, seq: int) -> TripPlanStop:
    for stop in variant.stops:
        if stop.seq == seq:
            return stop
    raise ApiError(404, "not_found", "No such stop on this plan.")


def _open(plan_row: TripPlan, stop: TripPlanStop) -> None:
    """A stop changes only while its plan is on and before it is done."""
    if plan_row.status not in OPEN_STATUSES:
        raise ApiError(409, "plan_ended", f"This plan is {plan_row.status}.")
    if stop.done_at is not None:
        raise ApiError(409, "stop_done", "This stop is already done.")


def next_seq(variant: TripPlanVariant, seq: int) -> int | None:
    """The next stop still to do: the first after ``seq``, else the first left anywhere."""
    undone = sorted(s.seq for s in variant.stops if s.done_at is None and s.seq != seq)
    later = [s for s in undone if s > seq]
    if later:
        return later[0]
    return undone[0] if undone else None


async def _list_items(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, ShoppingListItem]:
    if not ids:
        return {}
    stmt = (
        select(ShoppingListItem)
        .where(ShoppingListItem.id.in_(ids))
        .options(selectinload(ShoppingListItem.amounts))
        .execution_options(populate_existing=True)
    )
    return {row.id: row for row in (await db.execute(stmt)).unique().scalars()}


async def _observed_at(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
    if not ids:
        return {}
    stmt = select(PriceObservation.id, PriceObservation.observed_at).where(
        PriceObservation.id.in_(ids)
    )
    return dict((await db.execute(stmt)).all())


async def _products(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, Product]:
    if not ids:
        return {}
    rows = (await db.execute(select(Product).where(Product.id.in_(ids)))).scalars()
    return {p.id: p for p in rows}


async def _ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient:
    stmt = (
        select(Ingredient)
        .options(selectinload(Ingredient.measures))
        .where(Ingredient.id == ingredient_id)
    )
    return (await db.execute(stmt)).scalar_one()


async def _check_unit(db: AsyncSession, unit: str) -> None:
    if unit not in await load_units(db):
        raise ApiError(422, "unknown_unit", "unit is not a known unit code.")


# --- figures ---------------------------------------------------------------------------------


def _multiplier(item: TripPlanStopItem) -> Decimal:
    """How many of the priced thing the item takes: its packs, or one of a loose amount."""
    return item.packs if item.packs is not None else Decimal(1)


def effective_cost(item: TripPlanStopItem) -> Decimal:
    """What the item comes to: the plan's line cost, or packs of the edited price."""
    if item.actual_price is None:
        return item.line_cost
    return money(CTX.multiply(_multiplier(item), item.actual_price))


def _label(list_item: ShoppingListItem) -> str:
    if list_item.kind == "product" and list_item.product is not None:
        return list_item.product.name
    if list_item.ingredient is not None:
        return list_item.ingredient.name
    return str(list_item.id)


def _amounts(list_item: ShoppingListItem) -> list[AmountOut]:
    return [AmountOut(seq=a.seq, amount=a.amount, unit=a.unit) for a in list_item.amounts]


def _user(user: AppUser | None) -> UserBrief | None:
    return None if user is None else UserBrief(id=user.id, display_name=user.display_name)


# --- the stop screen -------------------------------------------------------------------------


async def stop_screen(
    db: AsyncSession, plan_row: TripPlan, seq: int, settings: Settings
) -> StopScreenOut:
    """One stop of the chosen variant, laid out as 10 lays it out (UI-4.8)."""
    variant = _chosen(plan_row)
    stop = _stop(variant, seq)
    home = await db.get(HomeBase, plan_row.home_base_id)
    assert home is not None

    later = [s for s in variant.stops if s.seq != seq and s.done_at is None]
    wanted = [i.list_item_id for i in stop.items] + [i.list_item_id for s in later for i in s.items]
    list_items = await _list_items(db, wanted)
    observed = await _observed_at(db, [i.observation_id for i in stop.items])

    def created(item: TripPlanStopItem) -> tuple[datetime, str]:
        row = list_items[item.list_item_id]
        return row.created_at, str(row.id)

    items: list[StopScreenItem] = []
    for item in sorted(stop.items, key=created):  # oldest first, as the list shows them
        row = list_items[item.list_item_id]
        items.append(
            StopScreenItem(
                id=item.id,
                list_item_id=item.list_item_id,
                label=_label(row),
                amounts=_amounts(row),
                note=row.note,
                product=ProductBrief.model_validate(item.product),
                packs=item.packs,
                pack_price=item.pack_price,
                line_cost=item.line_cost,
                observation_id=item.observation_id,
                planned_observed_at=observed[item.observation_id],
                price_stale=item.price_stale,
                forced=item.forced,
                got_here=item.got_here,
                actual_price=item.actual_price,
                actual_qty=item.actual_qty,
                actual_unit=item.actual_unit,
                edited=item.actual_price is not None,
                effective_cost=effective_cost(item),
                claimed_by=_user(row.claimed_by_user),
                checked_at=row.checked_at,
                checked_by=_user(row.checked_by_user),
            )
        )

    # "Cheaper at stop {m}": a later stop's unchecked items this location can fill.
    elsewhere = [
        (s, i)
        for s in later
        for i in s.items
        if i.list_item_id in list_items and list_items[i.list_item_id].checked_at is None
    ]
    cheaper: list[CheaperElsewhereItem] = []
    if elsewhere and stop.done_at is None:
        book = await trip_offers.offers(
            db,
            [list_items[i.list_item_id] for _, i in elsewhere],
            [stop.vendor_location_id],
            settings,
        )
        products = await _products(db, {o.product_id for o in book.offers.values()})
        for other, item in elsewhere:
            offer = book.offers.get((item.list_item_id, stop.vendor_location_id))
            if offer is None:
                continue
            row = list_items[item.list_item_id]
            cheaper.append(
                CheaperElsewhereItem(
                    id=item.id,
                    list_item_id=item.list_item_id,
                    label=_label(row),
                    amounts=_amounts(row),
                    at_seq=other.seq,
                    at_location_name=other.location.name,
                    product_there=ProductBrief.model_validate(item.product),
                    line_cost_there=effective_cost(item),
                    product_here=ProductBrief.model_validate(products[offer.product_id]),
                    packs_here=offer.packs,
                    pack_price_here=offer.pack_price,
                    line_cost_here=offer.line_cost,
                    price_stale_here=offer.stale,
                    difference=money(offer.line_cost - effective_cost(item)),
                )
            )

    actual_cost, lines_recorded = trip_planner.stop_actual(stop)
    location = stop.location
    return StopScreenOut(
        plan_id=plan_row.id,
        plan_status=plan_row.status,  # type: ignore[arg-type]
        list_id=plan_row.list_id,
        home_base_id=home.id,
        home_base_name=home.name,
        stop_id=stop.id,
        seq=stop.seq,
        stop_count=len(variant.stops),
        location=StopLocation(
            id=location.id,
            name=location.name,
            vendor_name=location.vendor.name,
            address=location.address or location.osm_address,
        ),
        arrive_at=stop.arrive_at,
        predicted_cost=stop.subtotal,
        estimated_here=money(sum((i.effective_cost for i in items), Decimal(0))),
        done_at=stop.done_at,
        purchase_id=stop.purchase_id,
        lines_recorded=lines_recorded,
        actual_cost=actual_cost,
        next_seq=next_seq(variant, seq),
        items=items,
        cheaper_elsewhere=cheaper,
    )


async def _refreshed(
    db: AsyncSession, plan_id: uuid.UUID, seq: int, settings: Settings
) -> StopScreenOut:
    return await stop_screen(db, await trip_planner.get_plan(db, plan_id), seq, settings)


# --- edits in the aisle ----------------------------------------------------------------------


async def edit_price(
    db: AsyncSession,
    plan_row: TripPlan,
    seq: int,
    item_id: uuid.UUID,
    payload: StopPriceIn | None,
    settings: Settings,
) -> StopScreenOut:
    """Hold a price read in the aisle on the stop item; ``None`` clears the edit.
    Records nothing: what it records is decided by "Done here"."""
    variant = _chosen(plan_row)
    stop = _stop(variant, seq)
    _open(plan_row, stop)
    item = next((i for i in stop.items if i.id == item_id), None)
    if item is None:
        raise ApiError(404, "not_found", "No such item at this stop.")
    if payload is None:
        item.actual_price = item.actual_qty = item.actual_unit = None
    else:
        await _check_unit(db, payload.unit)
        item.actual_price = payload.price
        item.actual_qty = payload.qty
        item.actual_unit = payload.unit
    await db.commit()
    return await _refreshed(db, plan_row.id, seq, settings)


async def get_here(
    db: AsyncSession, plan_row: TripPlan, seq: int, payload: GetHereIn, settings: Settings
) -> StopScreenOut:
    """Move another stop's item to this stop, re-priced at this location's offer
    or at the price given; without either it is a 422 ``not_offered_here``."""
    variant = _chosen(plan_row)
    stop = _stop(variant, seq)
    _open(plan_row, stop)
    found = next(
        ((s, i) for s in variant.stops if s.seq != seq for i in s.items if i.id == payload.item_id),
        None,
    )
    if found is None:
        raise ApiError(404, "not_found", "No such item at another stop of this plan.")
    source, item = found
    if source.done_at is not None:
        raise ApiError(409, "stop_done", "That item's stop is already done.")
    if any(i.list_item_id == item.list_item_id for i in stop.items):
        raise ApiError(409, "already_here", "That item is already at this stop.")
    list_item = (await _list_items(db, [item.list_item_id]))[item.list_item_id]
    book = await trip_offers.offers(db, [list_item], [stop.vendor_location_id], settings)
    offer = book.offers.get((item.list_item_id, stop.vendor_location_id))
    if offer is None and payload.price is None:
        raise ApiError(
            422,
            "not_offered_here",
            "This location has no price for that item; give the price read here.",
        )
    if payload.price is not None:
        await _check_unit(db, payload.unit)
    source.items.remove(item)
    stop.items.append(item)
    item.got_here = True
    if offer is not None:
        item.product_id = offer.product_id
        item.packs = offer.packs
        item.pack_price = offer.pack_price
        item.line_cost = offer.line_cost
        item.observation_id = offer.observation_id
        item.price_stale = offer.stale
    if payload.price is not None:
        item.actual_price = payload.price
        item.actual_qty = payload.qty
        item.actual_unit = payload.unit
    await db.commit()
    return await _refreshed(db, plan_row.id, seq, settings)


# --- "Done here" -----------------------------------------------------------------------------


async def _line_for(
    db: AsyncSession, item: TripPlanStopItem, list_item: ShoppingListItem, units
) -> LineIn:
    """The purchase line for a checked item: packs of the edited price, else the
    planned pack price times packs, else the amounts at the plan's unit price."""
    multiplier = _multiplier(item)
    if item.actual_price is not None:
        assert item.actual_qty is not None and item.actual_unit is not None
        qty = CTX.multiply(multiplier, item.actual_qty)
        total = money(CTX.multiply(multiplier, item.actual_price))
        unit = item.actual_unit
    elif item.packs is not None:
        qty, unit, total = item.packs, "each", item.line_cost
    else:
        product = item.product
        ingredient = await _ingredient(db, product.ingredient_id)
        pairs = [(a.amount, a.unit) for a in list_item.amounts]
        converted = trip_offers.amounts_in_canonical(
            pairs, list_item.kind, ingredient, product, units
        )
        qty = converted[0] if converted is not None else Decimal(0)
        unit, total = ingredient.canonical_unit, item.line_cost
    if qty <= 0:
        raise ApiError(
            422,
            "no_quantity",
            "The item has no quantity to record; edit its price with the quantity bought.",
            {"item_id": str(item.id)},
        )
    return LineIn(product_id=item.product_id, qty=qty, unit=unit, line_total=total)


async def lock_plan(db: AsyncSession, plan_id: uuid.UUID) -> None:
    """Hold the plan's row for this transaction, so two phones pressing "Done
    here" on one stop record it once: the second waits, then finds it done."""
    await db.execute(select(TripPlan.id).where(TripPlan.id == plan_id).with_for_update())


async def done(
    db: AsyncSession, user: AppUser, plan_row: TripPlan, seq: int, payload: StopDoneIn
) -> StopDoneOut:
    """Record the stop (S1); the router replays a retried key before reaching
    here, and takes ``lock_plan`` before loading the plan."""
    variant = _chosen(plan_row)
    stop = _stop(variant, seq)
    _open(plan_row, stop)
    by_id = {i.id: i for i in stop.items}
    unknown = [str(i) for i in payload.checked_item_ids if i not in by_id]
    if unknown:
        raise ApiError(422, "unknown_item", "No such item at this stop.", {"item_ids": unknown})
    checked_ids = set(payload.checked_item_ids)
    checked_items = [i for i in stop.items if i.id in checked_ids]
    list_items = await _list_items(db, [i.list_item_id for i in stop.items])
    units = await load_units(db)
    now = datetime.now(UTC)

    lines: list[LineIn] = []
    flags: dict[int, list[str]] = {}
    for position, item in enumerate(checked_items, start=1):
        lines.append(await _line_for(db, item, list_items[item.list_item_id], units))
        if item.actual_price is None:
            flags[position] = [purchases.PLANNED_PRICE]
    purchase = None
    observation_ids: list[uuid.UUID] = []
    if lines:
        purchase = await purchases.create_manual_core(
            db,
            user,
            ManualPurchaseIn(
                vendor_location_id=stop.vendor_location_id, purchased_at=now, lines=lines
            ),
            trip_plan_id=plan_row.id,
            line_flags=flags,
        )
        stop.purchase_id = purchase.id
        live = await purchases.live_observations(db, purchase)
        observation_ids = [live[line.id] for line in purchase.lines]

    # A price edited but not bought: one shelf observation, as POST /price-observations writes.
    shelf_ids: list[uuid.UUID] = []
    for item in stop.items:
        if item.id in checked_ids or item.actual_price is None:
            continue
        assert item.actual_qty is not None and item.actual_unit is not None
        observation = await pricebook.observe(
            db,
            product_id=item.product_id,
            vendor_location_id=stop.vendor_location_id,
            price=item.actual_price,
            qty=item.actual_qty,
            unit=item.actual_unit,
            source="shelf",
            entered_by=user,
            observed_at=now,
        )
        shelf_ids.append(observation.id)

    checked = 0
    for item in checked_items:
        row = list_items[item.list_item_id]
        if row.checked_at is None:
            row.checked_at = now
            row.checked_by = user.id
            checked += 1
        row.claimed_by = None
        row.claimed_at = None

    stop.done_at = now
    if plan_row.status == "planned":
        plan_row.status = "underway"
    if all(s.done_at is not None for s in variant.stops):
        plan_row.status = "done"
        plan_row.finished_at = now
    # Flushed, not committed: the router commits it with the Idempotency key, so a
    # retried key never finds the stop done without its stored answer.
    await db.flush()
    return StopDoneOut(
        plan_id=plan_row.id,
        stop_id=stop.id,
        seq=stop.seq,
        status=plan_row.status,  # type: ignore[arg-type]
        done_at=now,
        purchase_id=purchase.id if purchase is not None else None,
        lines_recorded=len(lines),
        observation_ids=observation_ids,
        shelf_observation_ids=shelf_ids,
        checked=checked,
        next_seq=next_seq(variant, seq),
    )
