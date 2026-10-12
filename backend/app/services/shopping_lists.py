"""Shopping lists (17, Phase 4A): one open list per kitchen, its items and their
(amount, unit) pairs, soft claims and check-offs, recipes folded in, archiving.

Every function takes an explicit kitchen or list; nothing here reads the
current-kitchen setting (09, kitchen scoping). Amounts are ``Decimal`` end to
end; conversion goes through ``app.units.convert`` in the ingredient's context
and never guesses: a pair that does not convert stays its own row.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ApiError
from app.models import AppUser, Ingredient, Product, Recipe, RecipeIngredient, RecipePin
from app.models.geo import HomeBase
from app.models.plan import ShoppingList, ShoppingListItem, ShoppingListItemQty
from app.schemas.catalog import IngredientSummary
from app.schemas.plan import (
    AmountIn,
    AmountOut,
    ArchiveOut,
    FromRecipeOut,
    ItemCreate,
    ItemOut,
    ItemPatch,
    ListOut,
    ListStatus,
    ListSummary,
    ProductBrief,
    SkippedLine,
    UserBrief,
)
from app.services.recipe_costing import from_unit_of
from app.services.units import load_units
from app.units import ConversionContext, ConversionFailure, Measure, convert
from app.units.table import Unit

Pair = tuple[Decimal, str]


# --- lookups ----------------------------------------------------------------------------


async def get_home_base(db: AsyncSession, home_base_id: uuid.UUID) -> HomeBase:
    row = await db.get(HomeBase, home_base_id)
    if row is None:
        raise ApiError(404, "not_found", "No such kitchen.")
    return row


def _items_query(list_id: uuid.UUID):
    return (
        select(ShoppingListItem)
        .where(ShoppingListItem.list_id == list_id)
        .options(selectinload(ShoppingListItem.amounts))
        .order_by(ShoppingListItem.created_at, ShoppingListItem.id)
        .execution_options(populate_existing=True)
    )


async def _items(db: AsyncSession, list_id: uuid.UUID) -> list[ShoppingListItem]:
    return list((await db.execute(_items_query(list_id))).unique().scalars())


async def get_item(db: AsyncSession, item_id: uuid.UUID) -> ShoppingListItem:
    stmt = (
        select(ShoppingListItem)
        .where(ShoppingListItem.id == item_id)
        .options(
            selectinload(ShoppingListItem.amounts), selectinload(ShoppingListItem.shopping_list)
        )
        .execution_options(populate_existing=True)
    )
    row = (await db.execute(stmt)).unique().scalar_one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "No such list item.")
    return row


def _writable(item: ShoppingListItem) -> ShoppingListItem:
    """An archived list stays readable; its items no longer change."""
    if item.shopping_list.status != "open":
        raise ApiError(409, "list_archived", "This item is on an archived list.")
    return item


async def open_list(db: AsyncSession, home_base_id: uuid.UUID) -> ShoppingList:
    """The kitchen's one open list, created on first read.

    Two first reads race on the partial unique index: the loser's insert fails,
    its savepoint rolls back, and it reads the winner's list.
    """
    await get_home_base(db, home_base_id)
    stmt = select(ShoppingList).where(
        ShoppingList.home_base_id == home_base_id, ShoppingList.status == "open"
    )
    found = (await db.execute(stmt)).scalar_one_or_none()
    if found is not None:
        return found
    try:
        async with db.begin_nested():
            db.add(ShoppingList(home_base_id=home_base_id, status="open"))
            await db.flush()
    except IntegrityError:
        pass  # the other first read won; its row is committed, or will be
    else:
        await db.commit()
    # Re-read either way, so the row comes with its kitchen loaded.
    found = (await db.execute(stmt)).scalar_one_or_none()
    if found is None:  # pragma: no cover - the winner's row is committed by now
        raise ApiError(409, "list_changed", "The list changed under you; reload.")
    return found


async def get_list(db: AsyncSession, home_base_id: uuid.UUID, list_id: uuid.UUID) -> ShoppingList:
    """One list of the kitchen, open or archived, read-only."""
    row = await db.get(ShoppingList, list_id)
    if row is None or row.home_base_id != home_base_id:
        raise ApiError(404, "not_found", "No such list for that kitchen.")
    return row


# --- output -------------------------------------------------------------------------------


def _user(user: AppUser | None) -> UserBrief | None:
    return None if user is None else UserBrief(id=user.id, display_name=user.display_name)


def item_out(item: ShoppingListItem) -> ItemOut:
    added_by = _user(item.added_by_user)
    assert added_by is not None
    return ItemOut(
        id=item.id,
        list_id=item.list_id,
        kind=item.kind,  # type: ignore[arg-type]
        ingredient=(IngredientSummary.model_validate(item.ingredient) if item.ingredient else None),
        product=ProductBrief.model_validate(item.product) if item.product else None,
        amounts=[AmountOut(seq=a.seq, amount=a.amount, unit=a.unit) for a in item.amounts],
        min_quality=item.min_quality,
        note=item.note,
        deferrable=item.deferrable,
        added_by=added_by,
        added_from=item.added_from,  # type: ignore[arg-type]
        recipe_id=item.recipe_id,
        carried_from_item_id=item.carried_from_item_id,
        claimed_by=_user(item.claimed_by_user),
        claimed_at=item.claimed_at,
        checked_by=_user(item.checked_by_user),
        checked_at=item.checked_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def list_out(db: AsyncSession, lst: ShoppingList) -> ListOut:
    items = await _items(db, lst.id)
    unchecked = [item_out(i) for i in items if i.checked_at is None]
    checked = sorted(
        (item_out(i) for i in items if i.checked_at is not None),
        key=lambda i: i.checked_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return ListOut(
        id=lst.id,
        home_base_id=lst.home_base_id,
        home_base_name=lst.home_base.name,
        status=lst.status,  # type: ignore[arg-type]
        created_at=lst.created_at,
        archived_at=lst.archived_at,
        unchecked=unchecked,
        checked=checked,
    )


async def list_lists(
    db: AsyncSession, home_base_id: uuid.UUID, status: ListStatus | None
) -> list[ListSummary]:
    """The kitchen's lists, newest first, with their item counts; archived by default."""
    await get_home_base(db, home_base_id)
    total = (
        select(ShoppingListItem.list_id, func.count().label("n"))
        .group_by(ShoppingListItem.list_id)
        .subquery()
    )
    unchecked = (
        select(ShoppingListItem.list_id, func.count().label("n"))
        .where(ShoppingListItem.checked_at.is_(None))
        .group_by(ShoppingListItem.list_id)
        .subquery()
    )
    stmt = (
        select(ShoppingList, func.coalesce(total.c.n, 0), func.coalesce(unchecked.c.n, 0))
        .outerjoin(total, total.c.list_id == ShoppingList.id)
        .outerjoin(unchecked, unchecked.c.list_id == ShoppingList.id)
        .where(ShoppingList.home_base_id == home_base_id)
        .order_by(ShoppingList.created_at.desc(), ShoppingList.id.desc())
    )
    if status is not None:
        stmt = stmt.where(ShoppingList.status == status)
    rows = (await db.execute(stmt)).unique().all()
    return [
        ListSummary(
            id=lst.id,
            home_base_id=lst.home_base_id,
            status=lst.status,
            created_at=lst.created_at,
            archived_at=lst.archived_at,
            item_count=int(n),
            unchecked_count=int(u),
        )
        for lst, n, u in rows
    ]


# --- amounts ------------------------------------------------------------------------------


def _check_units(pairs: list[AmountIn], units: Mapping[str, Unit]) -> list[Pair]:
    out: list[Pair] = []
    for pair in pairs:
        if pair.unit not in units:
            raise ApiError(
                422, "unknown_unit", f"'{pair.unit}' is not a unit.", {"unit": pair.unit}
            )
        out.append((pair.amount, pair.unit))
    return out


def _tidy(value: Decimal) -> Decimal:
    """The same number without trailing zeros (and never in exponent form)."""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def _merge_pairs(item: ShoppingListItem, pairs: list[Pair]) -> None:
    """Fold pairs into the item: same unit adds up, another unit is its own row."""
    next_seq = max((a.seq for a in item.amounts), default=0) + 1
    for amount, unit in pairs:
        row = next((a for a in item.amounts if a.unit == unit), None)
        if row is not None:
            row.amount = _tidy(row.amount + amount)
        else:
            item.amounts.append(ShoppingListItemQty(seq=next_seq, amount=_tidy(amount), unit=unit))
            next_seq += 1


def _replace_pairs(item: ShoppingListItem, pairs: list[Pair]) -> None:
    item.amounts.clear()
    for seq, (amount, unit) in enumerate(pairs, start=1):
        item.amounts.append(ShoppingListItemQty(seq=seq, amount=_tidy(amount), unit=unit))


# --- items --------------------------------------------------------------------------------


async def add_item(
    db: AsyncSession, lst: ShoppingList, user: AppUser, payload: ItemCreate
) -> ShoppingListItem:
    if lst.status != "open":
        raise ApiError(409, "list_archived", "This list is archived.")
    for model, target, what in (
        (Ingredient, payload.ingredient_id, "ingredient"),
        (Product, payload.product_id, "product"),
        (Recipe, payload.recipe_id, "recipe"),
    ):
        if target is not None and await db.get(model, target) is None:
            raise ApiError(404, "not_found", f"No such {what}.")
    pairs = _check_units(payload.amounts, await load_units(db))
    item = ShoppingListItem(
        list_id=lst.id,
        kind=payload.kind,
        ingredient_id=payload.ingredient_id,
        product_id=payload.product_id,
        min_quality=payload.min_quality,
        note=payload.note,
        deferrable=payload.deferrable,
        added_by=user.id,
        added_from=payload.added_from,
        recipe_id=payload.recipe_id,
    )
    _replace_pairs(item, pairs)
    db.add(item)
    await db.commit()
    return await get_item(db, item.id)


def _same_instant(a: datetime, b: datetime) -> bool:
    if a.tzinfo is None:
        a = a.replace(tzinfo=UTC)
    if b.tzinfo is None:
        b = b.replace(tzinfo=UTC)
    return a.astimezone(UTC) == b.astimezone(UTC)


async def update_item(
    db: AsyncSession, item: ShoppingListItem, payload: ItemPatch
) -> ShoppingListItem:
    """Edit the stated fields; ``updated_at`` must be the row the client last saw."""
    _writable(item)
    if not _same_instant(payload.updated_at, item.updated_at):
        raise ApiError(
            409,
            "stale_item",
            "This item changed since you loaded it; reload.",
            {"item": item_out(item).model_dump(mode="json")},
        )
    fields = payload.model_fields_set
    if "min_quality" in fields and payload.min_quality is not None and item.kind == "product":
        raise ApiError(422, "min_quality_on_product", "min_quality applies to ingredient items.")
    if "amounts" in fields and payload.amounts is not None:
        pairs = _check_units(payload.amounts, await load_units(db))
        item.amounts.clear()
        await db.flush()  # the old rows go before the new ones take their seq
        _replace_pairs(item, pairs)
    if "note" in fields:
        item.note = payload.note
    if "deferrable" in fields and payload.deferrable is not None:
        item.deferrable = payload.deferrable
    if "min_quality" in fields:
        item.min_quality = payload.min_quality
    item.updated_at = datetime.now(UTC)
    await db.commit()
    return await get_item(db, item.id)


async def remove_item(db: AsyncSession, item: ShoppingListItem) -> None:
    _writable(item)
    await db.delete(item)
    await db.commit()


async def claim(db: AsyncSession, item: ShoppingListItem, user: AppUser) -> ShoppingListItem:
    """A soft claim: who is getting it, shown beside the item; a later claim replaces it."""
    _writable(item)
    if item.claimed_by != user.id:
        item.claimed_by = user.id
        item.claimed_at = datetime.now(UTC)
        await db.commit()
    return await get_item(db, item.id)


async def unclaim(db: AsyncSession, item: ShoppingListItem) -> ShoppingListItem:
    _writable(item)
    if item.claimed_by is not None:
        item.claimed_by = None
        item.claimed_at = None
        await db.commit()
    return await get_item(db, item.id)


async def check(db: AsyncSession, item: ShoppingListItem, user: AppUser) -> ShoppingListItem:
    """Check off: sets who and when, clears the claim. Checking a checked item changes nothing."""
    _writable(item)
    if item.checked_at is None:
        item.checked_at = datetime.now(UTC)
        item.checked_by = user.id
        item.claimed_by = None
        item.claimed_at = None
        await db.commit()
    return await get_item(db, item.id)


async def uncheck(db: AsyncSession, item: ShoppingListItem) -> ShoppingListItem:
    _writable(item)
    if item.checked_at is not None:
        item.checked_at = None
        item.checked_by = None
        await db.commit()
    return await get_item(db, item.id)


# --- from a recipe --------------------------------------------------------------------------


async def _survivor(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient | None:
    """The ingredient a line resolves to today, following merges (as costing does)."""
    stmt = select(Ingredient).options(selectinload(Ingredient.measures))
    ingredient = (await db.execute(stmt.where(Ingredient.id == ingredient_id))).scalar_one_or_none()
    for _ in range(8):
        if ingredient is None or ingredient.merged_into is None:
            break
        ingredient = (
            await db.execute(stmt.where(Ingredient.id == ingredient.merged_into))
        ).scalar_one_or_none()
    return ingredient


def _context(ingredient: Ingredient, units: Mapping[str, Unit]) -> ConversionContext:
    return ConversionContext(
        canonical_unit=ingredient.canonical_unit,
        density_g_per_ml=ingredient.density_g_per_ml,
        density_source=ingredient.density_source,
        density_confirmed=ingredient.density_confirmed,
        measures=tuple(
            Measure(m.label, m.canonical_qty, m.source, m.confirmed) for m in ingredient.measures
        ),
        units=units,
    )


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass
class LineQuantity:
    pairs: list[Pair] = field(default_factory=list)
    note: str | None = None


def line_quantity(
    line: RecipeIngredient, scale: Decimal, ingredient: Ingredient, units: Mapping[str, Unit]
) -> LineQuantity:
    """What a recipe line adds to the list, scaled.

    A quantity that converts is one canonical pair; a ``ConversionFailure`` keeps
    the quantity in its own unit; a range takes its high; a text or absent
    quantity is a note and no pair. A failed quantity whose unit is a measure
    label rather than a unit code cannot be a row (``unit`` is a foreign key),
    so it becomes the note too.
    """
    if line.qty_kind in ("text", "none") or line.qty is None:
        return LineQuantity(note=line.qty_text.strip() if line.qty_text else None)
    qty = line.qty_high if line.qty_kind == "range" and line.qty_high is not None else line.qty
    qty = qty * scale
    from_unit = from_unit_of(line)
    result = convert(qty, from_unit, _context(ingredient, units))
    if not isinstance(result, ConversionFailure):
        return LineQuantity(pairs=[(result.qty, result.unit)])
    if from_unit in units:
        return LineQuantity(pairs=[(qty, from_unit)])
    return LineQuantity(note=f"{_plain(qty)} {from_unit}")


def _append_note(item: ShoppingListItem, note: str | None) -> None:
    if not note:
        return
    if not item.note:
        item.note = note
    elif note not in item.note:
        item.note = f"{item.note}; {note}"


async def add_from_recipe(
    db: AsyncSession,
    lst: ShoppingList,
    user: AppUser,
    recipe_id: uuid.UUID,
    scale: Decimal = Decimal(1),
) -> FromRecipeOut:
    """Add the recipe's non-negligible lines, scaled; see ``line_quantity`` for amounts.

    A pinned line adds a product item for the pinned product; any other resolved
    line adds an ingredient item. A line whose target is already on the open
    list, unchecked, folds into that item. Unmatched names are skipped and
    reported.
    """
    if lst.status != "open":
        raise ApiError(409, "list_archived", "This list is archived.")
    recipe = await db.get(Recipe, recipe_id)
    if recipe is None:
        raise ApiError(404, "not_found", "No such recipe.")
    lines = (
        await db.execute(
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.seq)
        )
    ).scalars()
    pins = dict(
        (
            await db.execute(
                select(RecipePin.name_norm, RecipePin.product_id).where(
                    RecipePin.recipe_id == recipe_id
                )
            )
        ).all()
    )
    units = await load_units(db)

    existing: dict[tuple[str, uuid.UUID], ShoppingListItem] = {}
    for item in await _items(db, lst.id):
        if item.checked_at is not None:
            continue
        target = item.ingredient_id if item.kind == "ingredient" else item.product_id
        assert target is not None
        existing.setdefault((item.kind, target), item)

    added = merged = 0
    skipped: list[SkippedLine] = []
    for line in lines:
        # Negligible by name (salt, water) or not an ingredient: not on the list.
        # The indexer also flags a text or absent quantity ``negligible``; such a
        # line still adds its item, with the text as the note (17, 4A).
        if line.resolution in ("negligible", "ignored"):
            continue
        product = None
        pin = pins.get(line.name_norm)
        if pin is not None:
            product = await db.get(Product, pin)
        ingredient_id = line.ingredient_id or (product.ingredient_id if product else None)
        ingredient = await _survivor(db, ingredient_id) if ingredient_id else None
        if ingredient is None:
            skipped.append(SkippedLine(raw_name=line.raw_name, reason="unmatched"))
            continue
        quantity = line_quantity(line, scale, ingredient, units)
        key = ("product", product.id) if product is not None else ("ingredient", ingredient.id)
        item = existing.get(key)
        if item is None:
            item = ShoppingListItem(
                list_id=lst.id,
                kind=key[0],
                ingredient_id=None if product is not None else ingredient.id,
                product_id=product.id if product is not None else None,
                added_by=user.id,
                added_from="recipe",
                recipe_id=recipe_id,
                amounts=[],  # initialised, so a later merge never lazy-loads it
            )
            db.add(item)
            existing[key] = item
            added += 1
        else:
            merged += 1
        _merge_pairs(item, quantity.pairs)
        _append_note(item, quantity.note)
    await db.commit()
    return FromRecipeOut(list=await list_out(db, lst), added=added, merged=merged, skipped=skipped)


# --- archiving --------------------------------------------------------------------------------


async def archive_and_renew(
    db: AsyncSession, lst: ShoppingList, carry_over: bool = True
) -> ArchiveOut:
    """Archive the open list and open the next; unchecked items carry forward as
    new rows that remember the row they came from, claims cleared."""
    if lst.status != "open":
        raise ApiError(409, "list_archived", "This list is already archived.")
    items = await _items(db, lst.id)
    lst.status = "archived"
    lst.archived_at = datetime.now(UTC)
    renewed = ShoppingList(home_base_id=lst.home_base_id, status="open")
    db.add(renewed)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ApiError(409, "list_changed", "The list changed under you; reload.") from None
    if carry_over:
        for item in items:
            if item.checked_at is not None:
                continue
            copy = ShoppingListItem(
                list_id=renewed.id,
                kind=item.kind,
                ingredient_id=item.ingredient_id,
                product_id=item.product_id,
                min_quality=item.min_quality,
                note=item.note,
                deferrable=item.deferrable,
                added_by=item.added_by,
                added_from=item.added_from,
                recipe_id=item.recipe_id,
                carried_from_item_id=item.id,
            )
            _replace_pairs(copy, [(a.amount, a.unit) for a in item.amounts])
            db.add(copy)
    await db.commit()
    await db.refresh(lst)
    await db.refresh(renewed)
    return ArchiveOut(archived=await list_out(db, lst), list=await list_out(db, renewed))
