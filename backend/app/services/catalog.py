"""Ingredients, measures, products, typeahead, and the conversion bench."""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import func, literal, select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog import categories
from app.catalog.categories import CategoryKey
from app.core.errors import ApiError
from app.models.catalog import Ingredient, IngredientMeasure, Product
from app.schemas.catalog import (
    ConvertIn,
    ConvertOut,
    IngredientCreate,
    IngredientUpdate,
    LastPaid,
    MeasureCreate,
    MeasureUpdate,
    ProductCreate,
    ProductListItem,
    ProductUpdate,
    ProvenanceOut,
    SearchHit,
)
from app.services.pagination import decode_cursor, decode_keyset, encode_cursor, encode_keyset
from app.services.pricebook import recompute_for_ingredient, recompute_for_product
from app.services.units import build_context
from app.units import (
    CanonicalQty,
    Provenance,
    convert,
)

# --- text matching ---------------------------------------------------------


def like_escape(text: str) -> str:
    """Make text match literally inside a LIKE pattern.

    Postgres's LIKE escapes with a backslash by default, so escaping it, % and _
    is enough: a search for "%" then finds a "%" rather than everything.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- ingredients ------------------------------------------------------------


def _ingredient_query():
    return select(Ingredient).options(selectinload(Ingredient.measures))


async def get_ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient:
    row = (
        await db.execute(_ingredient_query().where(Ingredient.id == ingredient_id))
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "No such ingredient.")
    return row


async def list_ingredients(
    db: AsyncSession,
    *,
    q: str | None = None,
    include_inactive: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[Ingredient], str | None]:
    stmt = _ingredient_query().order_by(Ingredient.id).limit(limit + 1)
    if not include_inactive:
        stmt = stmt.where(Ingredient.active.is_(True))
    if q:
        stmt = stmt.where(func.lower(Ingredient.name).contains(q.lower(), autoescape=True))
    after = decode_cursor(cursor)
    if after is not None:
        stmt = stmt.where(Ingredient.id > after)
    rows = list((await db.execute(stmt)).scalars())
    next_cursor = encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    return rows[:limit], next_cursor


async def create_ingredient(db: AsyncSession, payload: IngredientCreate) -> Ingredient:
    ingredient = Ingredient(
        name=payload.name.strip(),
        category=payload.category,
        canonical_unit=payload.canonical_unit,
        density_g_per_ml=payload.density_g_per_ml,
        density_source=payload.density_source,
        density_confirmed=False,
        yield_pct=payload.yield_pct,
        perishability=payload.perishability,
        notes=payload.notes,
    )
    db.add(ingredient)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _ingredient_conflict(exc) from exc
    await db.commit()
    return await get_ingredient(db, ingredient.id)


def _ingredient_conflict(exc: IntegrityError) -> ApiError:
    if "uq_ingredient_name_lower" in str(exc.orig):
        return ApiError(409, "ingredient_name_taken", "An ingredient with that name exists.")
    return ApiError(409, "conflict", "The ingredient could not be saved.")


async def update_ingredient(
    db: AsyncSession, ingredient_id: uuid.UUID, payload: IngredientUpdate
) -> Ingredient:
    ingredient = await get_ingredient(db, ingredient_id)
    data = payload.model_dump(exclude_unset=True)
    if data.pop("clear_density", False):
        ingredient.density_g_per_ml = None
        ingredient.density_source = None
        ingredient.density_confirmed = False
    if "density_g_per_ml" in data and data["density_g_per_ml"] is not None:
        ingredient.density_g_per_ml = data.pop("density_g_per_ml")
        ingredient.density_source = data.pop("density_source")
        ingredient.density_confirmed = False  # a new value is unconfirmed until confirmed
    data.pop("density_g_per_ml", None)
    data.pop("density_source", None)
    if "name" in data:
        data["name"] = data["name"].strip()
    for key, value in data.items():
        setattr(ingredient, key, value)
    bridge_changed = bool(
        {"density_g_per_ml", "density_source", "clear_density", "canonical_unit"}
        & set(payload.model_dump(exclude_unset=True))
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _ingredient_conflict(exc) from exc
    if bridge_changed:
        await _after_ingredient_bridge_change(db, ingredient_id)
    return await get_ingredient(db, ingredient_id)


async def set_ingredient_active(
    db: AsyncSession, ingredient_id: uuid.UUID, active: bool
) -> Ingredient:
    ingredient = await get_ingredient(db, ingredient_id)
    ingredient.active = active
    await db.commit()
    return await get_ingredient(db, ingredient_id)


async def confirm_density(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient:
    ingredient = await get_ingredient(db, ingredient_id)
    if ingredient.density_g_per_ml is None:
        raise ApiError(409, "no_density", "The ingredient has no density to confirm.")
    ingredient.density_confirmed = True
    await db.commit()
    return await get_ingredient(db, ingredient_id)


# --- measures ---------------------------------------------------------------


async def get_measure(db: AsyncSession, measure_id: uuid.UUID) -> IngredientMeasure:
    row = await db.get(IngredientMeasure, measure_id)
    if row is None:
        raise ApiError(404, "not_found", "No such measure.")
    return row


async def add_measure(
    db: AsyncSession, ingredient_id: uuid.UUID, payload: MeasureCreate
) -> IngredientMeasure:
    await get_ingredient(db, ingredient_id)
    measure = IngredientMeasure(
        ingredient_id=ingredient_id,
        label=payload.label.strip(),
        canonical_qty=payload.canonical_qty,
        source=payload.source,
        confirmed=payload.confirmed,
    )
    db.add(measure)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "measure_label_taken", "That measure label already exists.") from exc
    await db.refresh(measure)
    await _after_ingredient_bridge_change(db, ingredient_id)
    return measure


async def update_measure(
    db: AsyncSession, measure_id: uuid.UUID, payload: MeasureUpdate
) -> IngredientMeasure:
    measure = await get_measure(db, measure_id)
    data = payload.model_dump(exclude_unset=True)
    if "label" in data:
        data["label"] = data["label"].strip()
    if "canonical_qty" in data or "source" in data:
        measure.confirmed = False
    for key, value in data.items():
        setattr(measure, key, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "measure_label_taken", "That measure label already exists.") from exc
    await db.refresh(measure)
    await _after_ingredient_bridge_change(db, measure.ingredient_id)
    return measure


async def confirm_measure(db: AsyncSession, measure_id: uuid.UUID) -> IngredientMeasure:
    measure = await get_measure(db, measure_id)
    measure.confirmed = True
    await db.commit()
    await db.refresh(measure)
    return measure


async def delete_measure(db: AsyncSession, measure_id: uuid.UUID) -> None:
    measure = await get_measure(db, measure_id)
    ingredient_id = measure.ingredient_id
    await db.delete(measure)
    await db.commit()
    await _after_ingredient_bridge_change(db, ingredient_id)


# --- products ---------------------------------------------------------------


def _product_query():
    return select(Product).options(selectinload(Product.ingredient))


async def get_product(db: AsyncSession, product_id: uuid.UUID) -> Product:
    row = (await db.execute(_product_query().where(Product.id == product_id))).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "No such product.")
    return row


async def list_products(
    db: AsyncSession,
    *,
    ingredient_id: uuid.UUID | None = None,
    include_inactive: bool = False,
    q: str | None = None,
    category: CategoryKey | None = None,
    barcode: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[ProductListItem], str | None]:
    """Products by name, a page at a time; with ``q``, the best ``limit`` matches by rank.

    Both filter on the server, so a search or a category finds products beyond the
    first page (D12). A ranked search has no next page, like the typeahead. A
    ``barcode`` is an exact match and ignores the other filters.
    """
    category_values = await _category_values(db, category) if category else None
    if barcode:
        hits = await search_products(db, barcode, limit)
        rows = await _products_by_id(db, [h.id for h in hits if h.barcode == barcode])
        next_cursor = None
    elif category_values == []:
        return [], None
    elif q:
        hits = await search_products(
            db,
            q,
            limit,
            include_inactive=include_inactive,
            ingredient_id=ingredient_id,
            category_values=category_values,
        )
        rows, next_cursor = await _products_by_id(db, [h.id for h in hits]), None
    else:
        rows, next_cursor = await _product_page(
            db,
            ingredient_id=ingredient_id,
            include_inactive=include_inactive,
            category_values=category_values,
            limit=limit,
            cursor=cursor,
        )
    paid = await _last_paid(db, [r.id for r in rows])
    items = [
        ProductListItem.model_validate(r).model_copy(update={"last_paid": paid.get(r.id)})
        for r in rows
    ]
    return items, next_cursor


async def _product_page(
    db: AsyncSession,
    *,
    ingredient_id: uuid.UUID | None,
    include_inactive: bool,
    category_values: list[str] | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[Product], str | None]:
    # Keyset on (lower(name), id), with lower(name) as Postgres computes it, so
    # the cursor compares exactly the way the ORDER BY sorts.
    sort_name = func.lower(Product.name)
    stmt = _product_query().add_columns(sort_name).order_by(sort_name, Product.id).limit(limit + 1)
    if not include_inactive:
        stmt = stmt.where(Product.active.is_(True))
    if ingredient_id is not None:
        stmt = stmt.where(Product.ingredient_id == ingredient_id)
    if category_values is not None:
        stmt = stmt.join(Product.ingredient).where(Ingredient.category.in_(category_values))
    after = decode_keyset(cursor)
    if after is not None:
        name_after, id_after = after
        stmt = stmt.where(
            tuple_(sort_name, Product.id)
            > tuple_(literal(name_after), literal(id_after, Product.id.type))
        )
    rows = (await db.execute(stmt)).all()
    next_cursor = (
        encode_keyset(rows[limit - 1][1], rows[limit - 1][0].id) if len(rows) > limit else None
    )
    return [product for product, _ in rows[:limit]], next_cursor


async def _products_by_id(db: AsyncSession, ids: list[uuid.UUID]) -> list[Product]:
    """Load products in one query, keeping the order of ``ids``."""
    if not ids:
        return []
    found = {
        p.id: p for p in (await db.execute(_product_query().where(Product.id.in_(ids)))).scalars()
    }
    return [found[i] for i in ids if i in found]


async def _category_values(db: AsyncSession, key: CategoryKey) -> list[str]:
    """The stored free-text categories that map to ``key``.

    Filtering on these exact values keeps the synonym map in one place,
    ``app/catalog/categories.py``, instead of a second copy in SQL.
    """
    stored = await db.execute(
        select(Ingredient.category).where(Ingredient.category.is_not(None)).distinct()
    )
    return [c for c in stored.scalars() if categories.key(c) == key]


_LAST_PAID_SQL = text(
    """
    SELECT DISTINCT ON (pc.product_id)
           pc.product_id, pc.price, pc.qty, pc.unit, pc.is_promo,
           v.id AS vendor_id, v.name AS vendor_name,
           pu.id AS purchase_id, pu.purchased_at AS paid_at
    FROM price_current pc
    JOIN purchase_line pl ON pl.id = pc.purchase_line_id
    JOIN purchase pu ON pu.id = pl.purchase_id
    JOIN vendor_location vl ON vl.id = pc.vendor_location_id
    JOIN vendor v ON v.id = vl.vendor_id
    WHERE pc.product_id = ANY(CAST(:ids AS uuid[])) AND pu.status = 'committed'
    ORDER BY pc.product_id, pu.purchased_at DESC, pc.observation_id DESC
    """
)


async def _last_paid(db: AsyncSession, product_ids: list[uuid.UUID]) -> dict[uuid.UUID, LastPaid]:
    """What each product last cost on a committed purchase, in one query for the page.

    A reopened purchase keeps its observations live until it is recommitted, so
    the purchase's status is checked here rather than trusting the observation.
    Shelf prices have no purchase line and are not "paid".
    """
    if not product_ids:
        return {}
    rows = await db.execute(_LAST_PAID_SQL, {"ids": product_ids})
    return {
        r["product_id"]: LastPaid.model_validate({k: v for k, v in r.items() if k != "product_id"})
        for r in rows.mappings()
    }


def _product_conflict(exc: IntegrityError) -> ApiError:
    message = str(exc.orig)
    if "uq_product_barcode" in message:
        return ApiError(409, "barcode_taken", "Another product already has that barcode.")
    if "uq_ingredient_name_lower" in message:
        return ApiError(409, "ingredient_name_taken", "An ingredient with that name exists.")
    if "pack_unit" in message or "unit.code" in message:
        return ApiError(422, "unknown_unit", "pack_unit is not a known unit code.")
    return ApiError(409, "conflict", "The product could not be saved.")


async def create_product(db: AsyncSession, payload: ProductCreate) -> Product:
    """Create a product, and its ingredient inline if asked, in one transaction."""
    try:
        if payload.ingredient is not None:
            spec = payload.ingredient
            ingredient = Ingredient(
                name=spec.name.strip(),
                category=spec.category,
                canonical_unit=spec.canonical_unit,
                density_g_per_ml=spec.density_g_per_ml,
                density_source=spec.density_source,
                density_confirmed=False,
                yield_pct=spec.yield_pct,
                perishability=spec.perishability,
                notes=spec.notes,
            )
            db.add(ingredient)
            await db.flush()
            ingredient_id = ingredient.id
        else:
            assert payload.ingredient_id is not None
            if await db.get(Ingredient, payload.ingredient_id) is None:
                raise ApiError(404, "not_found", "No such ingredient.")
            ingredient_id = payload.ingredient_id
        product = Product(
            ingredient_id=ingredient_id,
            brand=payload.brand,
            name=payload.name.strip(),
            pack_qty=payload.pack_qty,
            pack_unit=payload.pack_unit,
            barcode=payload.barcode,
            quality_rating=payload.quality_rating,
            exclusive_vendor_id=payload.exclusive_vendor_id,
            density_override=payload.density_override,
            density_override_source=payload.density_override_source,
            density_override_confirmed=False,
            notes=payload.notes,
        )
        db.add(product)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _product_conflict(exc) from exc
    except ApiError:
        await db.rollback()
        raise
    return await get_product(db, product.id)


async def update_product(
    db: AsyncSession, product_id: uuid.UUID, payload: ProductUpdate
) -> Product:
    product = await get_product(db, product_id)
    data = payload.model_dump(exclude_unset=True)
    if data.pop("clear_pack", False):
        product.pack_qty = None
        product.pack_unit = None
    if data.get("pack_qty") is not None:
        product.pack_qty = data.pop("pack_qty")
        product.pack_unit = data.pop("pack_unit")
    data.pop("pack_qty", None)
    data.pop("pack_unit", None)
    if data.pop("clear_barcode", False):
        product.barcode = None
    if data.pop("clear_exclusive_vendor", False):
        product.exclusive_vendor_id = None
    if data.pop("clear_density_override", False):
        product.density_override = None
        product.density_override_source = None
        product.density_override_confirmed = False
    if data.get("density_override") is not None:
        product.density_override = data.pop("density_override")
        product.density_override_source = data.pop("density_override_source")
        product.density_override_confirmed = False
    data.pop("density_override", None)
    data.pop("density_override_source", None)
    if data.get("ingredient_id") is not None:
        if await db.get(Ingredient, data["ingredient_id"]) is None:
            raise ApiError(404, "not_found", "No such ingredient.")
    else:
        data.pop("ingredient_id", None)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    for key, value in data.items():
        if value is None and key in {"name"}:
            continue
        setattr(product, key, value)
    bridge_changed = bool(
        {
            "pack_qty",
            "pack_unit",
            "clear_pack",
            "density_override",
            "density_override_source",
            "clear_density_override",
            "ingredient_id",
        }
        & set(payload.model_dump(exclude_unset=True))
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _product_conflict(exc) from exc
    if bridge_changed:
        await _after_product_bridge_change(db, product_id)
    return await get_product(db, product_id)


async def set_product_active(db: AsyncSession, product_id: uuid.UUID, active: bool) -> Product:
    product = await get_product(db, product_id)
    product.active = active
    await db.commit()
    return await get_product(db, product_id)


async def confirm_density_override(db: AsyncSession, product_id: uuid.UUID) -> Product:
    product = await get_product(db, product_id)
    if product.density_override is None:
        raise ApiError(409, "no_density", "The product has no density override to confirm.")
    product.density_override_confirmed = True
    await db.commit()
    return await get_product(db, product_id)


# --- typeahead --------------------------------------------------------------

_SEARCH_SQL = """
    SELECT p.id, p.name, p.brand, p.barcode, p.pack_qty, p.pack_unit, p.quality_rating,
           i.id AS ingredient_id, i.name AS ingredient_name, i.canonical_unit,
           i.active AS ingredient_active, i.category,
           coalesce(p.barcode = :q, false) AS barcode_hit,  -- NULL would sort first
           word_similarity(:ql, lower(p.name)) AS s_name,
           word_similarity(:ql, lower(coalesce(p.brand, ''))) AS s_brand,
           word_similarity(:ql, lower(i.name)) AS s_ingredient
    FROM product p
    JOIN ingredient i ON i.id = p.ingredient_id
    WHERE {filters} AND (
        p.barcode = :q
        OR lower(p.name) LIKE :like
        OR lower(coalesce(p.brand, '')) LIKE :like
        OR lower(i.name) LIKE :like
        OR :ql <% lower(p.name)
        OR :ql <% lower(i.name)
    )
    ORDER BY barcode_hit DESC,
             GREATEST(word_similarity(:ql, lower(p.name)),
                      word_similarity(:ql, lower(coalesce(p.brand, ''))),
                      word_similarity(:ql, lower(i.name))) DESC,
             (lower(p.name) LIKE :prefix) DESC,
             p.name
    LIMIT :limit
"""


async def search_products(
    db: AsyncSession,
    q: str,
    limit: int = 10,
    *,
    include_inactive: bool = False,
    ingredient_id: uuid.UUID | None = None,
    category_values: list[str] | None = None,
) -> list[SearchHit]:
    ql = q.strip().lower()
    if not ql:
        return []
    params: dict[str, object] = {
        "q": q.strip(),
        "ql": ql,
        "like": f"%{like_escape(ql)}%",
        "prefix": f"{like_escape(ql)}%",
        "limit": limit,
    }
    # Fixed fragments only; every value is a bound parameter.
    filters = [] if include_inactive else ["p.active AND i.active"]
    if ingredient_id is not None:
        filters.append("p.ingredient_id = :ingredient_id")
        params["ingredient_id"] = ingredient_id
    if category_values is not None:
        filters.append("i.category = ANY(CAST(:category_values AS text[]))")
        params["category_values"] = category_values
    sql = text(_SEARCH_SQL.format(filters=" AND ".join(filters) or "TRUE"))
    rows = await db.execute(sql, params)
    hits: list[SearchHit] = []
    for r in rows.mappings():
        scores = {
            "name": Decimal(str(r["s_name"])),
            "brand": Decimal(str(r["s_brand"])),
            "ingredient": Decimal(str(r["s_ingredient"])),
        }
        if r["barcode_hit"]:
            match, score = "barcode", Decimal("1")
        else:
            match = max(scores, key=lambda k: scores[k])
            score = scores[match]
        hits.append(
            SearchHit(
                id=r["id"],
                name=r["name"],
                brand=r["brand"],
                barcode=r["barcode"],
                pack_qty=r["pack_qty"],
                pack_unit=r["pack_unit"],
                quality_rating=r["quality_rating"],
                ingredient={
                    "id": r["ingredient_id"],
                    "name": r["ingredient_name"],
                    "canonical_unit": r["canonical_unit"],
                    "active": r["ingredient_active"],
                    "category": r["category"],
                },
                match=match,
                score=score.quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN),
            )
        )
    return hits


# --- bench -------------------------------------------------------------------


def provenance_out(p: Provenance) -> ProvenanceOut:
    return ProvenanceOut(
        bridge_kind=p.bridge_kind,
        source=p.source,
        confirmed=p.confirmed,
        detail=p.detail,
        via=provenance_out(p.via) if p.via is not None else None,
        rests_on_unconfirmed=p.rests_on_unconfirmed,
    )


async def bench(db: AsyncSession, ingredient_id: uuid.UUID, payload: ConvertIn) -> ConvertOut:
    ingredient = await get_ingredient(db, ingredient_id)
    product = None
    if payload.product_id is not None:
        product = await get_product(db, payload.product_id)
        if product.ingredient_id != ingredient.id:
            raise ApiError(422, "product_mismatch", "That product does not fulfil this ingredient.")
    context = await build_context(db, ingredient, product)
    result = convert(payload.qty, payload.unit, context)
    if isinstance(result, CanonicalQty):
        return ConvertOut(
            ok=True,
            qty=result.qty,
            unit=result.unit,
            provenance=provenance_out(result.provenance),
            version=result.version,
        )
    return ConvertOut(
        ok=False, failure_code=result.code, message=result.message, version=result.version
    )


# --- recompute hooks (price book) -------------------------------------------
#
# These were function-level imports to break a cycle: pricebook imported the
# cursor helpers and build_context from here. Those now live in
# services/pagination.py and services/units.py, pricebook no longer imports this
# module at all, and the dependency runs one way — so the import can say so.


async def _after_ingredient_bridge_change(db: AsyncSession, ingredient_id: uuid.UUID) -> None:
    await recompute_for_ingredient(db, ingredient_id)


async def _after_product_bridge_change(db: AsyncSession, product_id: uuid.UUID) -> None:
    await recompute_for_product(db, product_id)
