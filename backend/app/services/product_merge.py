"""Merging duplicate products (#179).

Merging A into B keeps B and retires A. Price observations are facts and are
never touched (non-negotiable 4): A gets ``merged_into = B`` and the price views
report A's observations under B, normalized against B's pack and density.
Everything else that names A, and may change, is re-pointed to B in the same
transaction: codes, vendor listings, photos, receipt aliases, receipt lines,
pending update proposals and open lookups. B's own fields are never changed.

Merges stay one level deep: anything already merged into A is re-pointed to B,
and a merged product can't be a survivor, so the views need one join.

A preview runs the same merge in the session and rolls it back, as the
ingredient merge does (1G).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import (
    Ingredient,
    LookupRequest,
    PriceNorm,
    PriceObservation,
    PriceObservationVoid,
    Product,
    ProductIdentifier,
    ProductImage,
    ProductProposal,
    PurchaseLine,
    ReceiptAlias,
    UnitRow,
    VendorListing,
)
from app.services import pricebook
from app.services.product_photos import reselect


@dataclass
class ProductMerge:
    survivor_id: uuid.UUID
    loser_id: uuid.UUID
    survivor_name: str
    loser_name: str
    prices: int
    listings: int
    codes: int
    photos: int
    aliases: int
    lines: int
    # Each product's pack unit: the survivor's is kept (30 oz over 30 fl oz).
    survivor_pack_unit: str | None
    loser_pack_unit: str | None
    # The unit the survivor's ingredient compares prices in, and the merged prices
    # recorded in another dimension (fl oz against g) that still need a density.
    compare_unit: str
    other_dimension_prices: int
    other_dimension_units: list[str] = field(default_factory=list)
    # Prices of either product that compared before the merge and don't after.
    prices_needing_bridge: int = 0


def _live(product_id: uuid.UUID):
    return (PriceObservation.product_id == product_id) & ~select(PriceObservationVoid.id).where(
        PriceObservationVoid.observation_id == PriceObservation.id
    ).exists()


async def _count(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one())


async def _failing(db: AsyncSession, survivor_id: uuid.UUID) -> int:
    """Live prices reported under the survivor whose normalization fails."""
    stmt = (
        select(func.count())
        .select_from(PriceObservation)
        .join(PriceNorm, PriceNorm.observation_id == PriceObservation.id)
        .where(
            PriceObservation.product_id.in_(pricebook.observed_as([survivor_id])),
            PriceNorm.status != "ok",
            ~select(PriceObservationVoid.id)
            .where(PriceObservationVoid.observation_id == PriceObservation.id)
            .exists(),
        )
    )
    return await _count(db, stmt)


async def _lock_pair(
    db: AsyncSession, survivor_id: uuid.UUID, loser_id: uuid.UUID
) -> tuple[Product, Product]:
    rows = (
        (
            await db.execute(
                select(Product)
                .where(Product.id.in_([survivor_id, loser_id]))
                .order_by(Product.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    found = {p.id: p for p in rows}
    if survivor_id not in found or loser_id not in found:
        raise ApiError(404, "not_found", "No such product.")
    return found[survivor_id], found[loser_id]


async def _compare_unit(db: AsyncSession, survivor_id: uuid.UUID) -> str:
    unit = await db.execute(
        select(Ingredient.canonical_unit)
        .join(Product, Product.ingredient_id == Ingredient.id)
        .where(Product.id == survivor_id)
    )
    return unit.scalar_one()


async def _other_dimension(
    db: AsyncSession, loser_id: uuid.UUID, unit: str
) -> tuple[int, list[str]]:
    """The merged product's prices in a mass or volume unit of another dimension
    than ``unit`` that don't compare once merged: they wait on a density. A count
    ("1 each") is priced through the survivor's pack instead."""
    dimension = select(UnitRow.dimension).where(UnitRow.code == unit).scalar_subquery()
    rows = (
        await db.execute(
            select(PriceObservation.unit, func.count())
            .join(UnitRow, UnitRow.code == PriceObservation.unit)
            .join(PriceNorm, PriceNorm.observation_id == PriceObservation.id)
            .where(
                _live(loser_id),
                UnitRow.dimension != dimension,
                UnitRow.dimension != "count",
                PriceNorm.status != "ok",
            )
            .group_by(PriceObservation.unit)
            .order_by(PriceObservation.unit)
        )
    ).all()
    return sum(n for _, n in rows), [u for u, _ in rows]


async def _move_photos(db: AsyncSession, survivor: Product, loser: Product) -> int:
    """Move the loser's photos. A photo the survivor already holds stays with the
    loser (one row per upload per product); the survivor keeps its main photo."""
    held = set(
        (
            await db.execute(
                select(ProductImage.upload_sha256).where(ProductImage.product_id == survivor.id)
            )
        ).scalars()
    )
    survivor_pinned = await _count(
        db,
        select(func.count())
        .select_from(ProductImage)
        .where(ProductImage.product_id == survivor.id, ProductImage.pinned),
    )
    moving = (
        (
            await db.execute(
                select(ProductImage).where(
                    ProductImage.product_id == loser.id,
                    ProductImage.upload_sha256.not_in(held) if held else true(),
                )
            )
        )
        .scalars()
        .all()
    )
    loser.primary_image_id = None
    await db.flush()
    for image in moving:
        image.product_id = survivor.id
        if survivor_pinned:
            image.pinned = False
            image.pinned_at = None
    await db.flush()
    if survivor.primary_image_id is None:
        await reselect(db, survivor)
    await reselect(db, loser)
    return len(moving)


async def _merge_in_session(
    db: AsyncSession, survivor_id: uuid.UUID, loser_id: uuid.UUID
) -> ProductMerge:
    """The merge itself, flushed and uncommitted. Returns what it did."""
    if survivor_id == loser_id:
        raise ApiError(422, "merge_self", "A product can't be merged into itself.")
    survivor, loser = await _lock_pair(db, survivor_id, loser_id)
    if survivor.merged_into is not None:
        raise ApiError(
            409, "merge_target_merged", "That product was itself merged; choose the one it became."
        )
    if not survivor.active:
        raise ApiError(409, "merge_target_inactive", "Choose an active product to keep.")
    if loser.merged_into is not None:
        raise ApiError(409, "already_merged", "This product was already merged.")

    survivor_name, loser_name = survivor.name, loser.name
    survivor_pack, loser_pack = survivor.pack_unit, loser.pack_unit
    prices = await _count(
        db, select(func.count()).select_from(PriceObservation).where(_live(loser.id))
    )
    before = await _failing(db, survivor.id) + await _failing(db, loser.id)

    def _repoint(model, *extra):
        return (
            update(model)
            .where(model.product_id == loser.id, *extra)
            .values(product_id=survivor.id)
            .execution_options(synchronize_session=False)
        )

    # 1. Anything already merged into the loser now names the survivor (one level).
    await db.execute(
        update(Product)
        .where(Product.merged_into == loser.id)
        .values(merged_into=survivor.id)
        .execution_options(synchronize_session=False)
    )
    # 2. Codes, listings, aliases and receipt lines. Codes are unique across all
    #    products, so a code can't be held twice.
    codes = (await db.execute(_repoint(ProductIdentifier))).rowcount
    listings = (await db.execute(_repoint(VendorListing))).rowcount
    aliases = (await db.execute(_repoint(ReceiptAlias))).rowcount
    lines = (await db.execute(_repoint(PurchaseLine))).rowcount
    # 3. Work still waiting on the loser: update proposals and open lookups.
    await db.execute(_repoint(ProductProposal, ProductProposal.status == "pending"))
    await db.execute(_repoint(LookupRequest, LookupRequest.status == "open"))
    # 4. Photos.
    photos = await _move_photos(db, survivor, loser)
    # 5. The loser leaves, naming the survivor.
    loser.active = False
    loser.merged_into = survivor.id
    await db.flush()
    # 6. The loser's prices, renormalized as the survivor's, in this transaction.
    db.expire_all()
    await pricebook.recompute_for_product_core(db, survivor_id)
    after = await _failing(db, survivor_id)
    compare_unit = await _compare_unit(db, survivor_id)
    other_prices, other_units = await _other_dimension(db, loser_id, compare_unit)
    return ProductMerge(
        survivor_id=survivor_id,
        loser_id=loser_id,
        survivor_name=survivor_name,
        loser_name=loser_name,
        prices=prices,
        listings=listings,
        codes=codes,
        photos=photos,
        aliases=aliases,
        lines=lines,
        survivor_pack_unit=survivor_pack,
        loser_pack_unit=loser_pack,
        compare_unit=compare_unit,
        other_dimension_prices=other_prices,
        other_dimension_units=other_units,
        prices_needing_bridge=max(after - before, 0),
    )


async def merge_preview(
    db: AsyncSession, survivor_id: uuid.UUID, loser_id: uuid.UUID
) -> ProductMerge:
    """What a merge would do, from a trial merge that is rolled back."""
    try:
        return await _merge_in_session(db, survivor_id, loser_id)
    finally:
        await db.rollback()


async def merge(db: AsyncSession, survivor_id: uuid.UUID, loser_id: uuid.UUID) -> ProductMerge:
    try:
        done = await _merge_in_session(db, survivor_id, loser_id)
    except BaseException:
        await db.rollback()
        raise
    await db.commit()
    return done
