"""Where a purchased item is kept and when it is best by (2Q, docs/spec/15).

An inferred date is the purchase's local date plus the ingredient's keep time for
the place the line is stored, and is absent when that time is. A printed use-by
date replaces it; a sell-by date does not (FSIS). A person may set or clear the
date. Only inferred dates are recomputed: when the purchase date is corrected,
the line is re-pointed, the line moves to another place, or the ingredient's keep
time changes. Printed and person dates are never overwritten. Prices are not
touched here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import keep
from app.core.errors import ApiError
from app.models import Ingredient, Product, Purchase, PurchaseLine
from app.schemas.purchases import LineKeepingIn
from app.services import purchases
from app.services.opening_hours import household_zone

FIXED = ("printed", "person")


def local_date(at: datetime) -> date:
    """The household's calendar date of a moment."""
    return at.astimezone(household_zone()).date()


def infer(
    line: PurchaseLine,
    ingredient: Ingredient | None,
    purchased_on: date,
    *,
    repointed: bool = False,
) -> None:
    """Default the line's place and recompute an inferred date. No I/O."""
    if line.line_kind != "item":
        return
    if ingredient is None:
        if repointed:
            line.stored_in = None
        if line.best_by_source == "inferred":
            line.best_by, line.best_by_source = None, None
        return
    if line.stored_in is None or repointed:
        line.stored_in = keep.stored_in(ingredient.perishability)
    if line.best_by_source in FIXED:
        return
    when = keep.best_by(purchased_on, getattr(ingredient, f"keep_{line.stored_in}_days"))
    line.best_by, line.best_by_source = when, ("inferred" if when is not None else None)


async def _ingredients(
    db: AsyncSession, product_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, Ingredient]:
    ids = {i for i in product_ids if i is not None}
    if not ids:
        return {}
    rows = await db.execute(
        select(Product.id, Ingredient)
        .join(Ingredient, Ingredient.id == Product.ingredient_id)
        .where(Product.id.in_(ids))
    )
    return dict(rows.tuples().all())


async def refresh(
    db: AsyncSession,
    purchase: Purchase,
    lines: Iterable[PurchaseLine] | None = None,
    *,
    repointed: Iterable[uuid.UUID] = (),
) -> None:
    """Infer places and dates for ``lines`` (default: all of the purchase's). No commit."""
    lines = list(purchase.lines if lines is None else lines)
    ingredients = await _ingredients(db, (line.product_id for line in lines))
    day = local_date(purchase.purchased_at)
    moved = set(repointed)
    for line in lines:
        ingredient = ingredients.get(line.product_id) if line.product_id else None
        infer(line, ingredient, day, repointed=line.id in moved)


async def refresh_ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> int:
    """Recompute the inferred dates of every line of an ingredient. No commit."""
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None:
        return 0
    rows = await db.execute(
        select(PurchaseLine, Purchase.purchased_at)
        .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
        .join(Product, Product.id == PurchaseLine.product_id)
        .where(
            Product.ingredient_id == ingredient_id,
            PurchaseLine.removed_at.is_(None),
            Purchase.status != "voided",
            or_(PurchaseLine.best_by_source.is_(None), PurchaseLine.best_by_source == "inferred"),
        )
    )
    count = 0
    for line, purchased_at in rows.tuples():
        infer(line, ingredient, local_date(purchased_at))
        count += 1
    return count


async def set_keeping(
    db: AsyncSession, purchase_id: uuid.UUID, line_id: uuid.UUID, payload: LineKeepingIn
) -> Purchase:
    """Move a line to another place, or give or clear its best-by date.

    Allowed at any status but voided: a label is often read after the receipt is
    committed. A ``sell_by`` date is not a best-by date; the inferred one stands.
    """
    purchase = await purchases.get_purchase(db, purchase_id, lock=True)
    purchases.ensure_not_voided(purchase)
    line = next((x for x in purchase.lines if x.id == line_id), None)
    if line is None:
        raise ApiError(404, "not_found", "No such line on this purchase.")
    if line.line_kind != "item":
        raise ApiError(422, "not_an_item", "Only item lines have a best-by date.")
    if payload.stored_in is not None:
        line.stored_in = payload.stored_in
    match payload.date:
        case "use_by":
            line.best_by, line.best_by_source = payload.best_by, "printed"
        case "set":
            line.best_by, line.best_by_source = payload.best_by, "person"
        case "clear":
            line.best_by, line.best_by_source = None, "person"
        case "sell_by" | "infer":
            line.best_by, line.best_by_source = None, None
    await refresh(db, purchase, [line])
    await db.commit()
    return await purchases.get_purchase(db, purchase_id)
