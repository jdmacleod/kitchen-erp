"""Ingredients, measures, products, typeahead, and the conversion bench."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from sqlalchemy import func, literal, or_, select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog import categories, standard
from app.catalog.attributes import validate_attributes
from app.catalog.categories import CategoryKey
from app.catalog.identifiers import (
    AmbiguousCode,
    InvalidGtin,
    classify_barcode,
    display,
    lookup_keys,
)
from app.catalog.names import normalize_name, singulars
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.models.catalog import (
    Ingredient,
    IngredientAlias,
    IngredientMeasure,
    IngredientRef,
    Product,
    ProductIdentifier,
)
from app.schemas.catalog import (
    ConvertIn,
    ConvertOut,
    IngredientCreate,
    IngredientMatch,
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
from app.services.normalize import normalize_receipt_text
from app.services.pagination import decode_cursor, decode_keyset, encode_cursor, encode_keyset
from app.services.pricebook import recompute_for_ingredient, recompute_for_product
from app.services.spellings import add_generated_spellings, add_spelling
from app.services.units import build_context, load_units
from app.units import (
    CanonicalQty,
    Provenance,
    convert,
)

log = get_logger(__name__)

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


_INGREDIENT_SEARCH_SQL = text(
    """
    WITH hits AS (
        SELECT i.id, NULL::text AS spelling,
               CASE WHEN lower(i.name) = :ql THEN 0
                    WHEN lower(i.name) LIKE :prefix THEN 1
                    ELSE 2 END AS rank,
               similarity(lower(i.name), :ql) AS sim
        FROM ingredient i
        WHERE i.active AND (lower(i.name) LIKE :contains OR lower(i.name) % :ql)
        UNION ALL
        SELECT a.ingredient_id, a.name_norm,
               CASE WHEN a.name_norm = :norm THEN 0
                    WHEN a.name_norm LIKE :nprefix THEN 1
                    ELSE 2 END,
               similarity(a.name_norm, :norm)
        FROM ingredient_alias a
        JOIN ingredient i ON i.id = a.ingredient_id AND i.active
        WHERE a.name_norm LIKE :ncontains OR a.name_norm % :norm
    ), best AS (
        SELECT DISTINCT ON (id) id, spelling, rank, sim
        FROM hits
        ORDER BY id, rank, spelling IS NOT NULL, sim DESC
    )
    SELECT i.id, i.name, i.category, i.canonical_unit, b.spelling, b.rank, b.sim
    FROM best b JOIN ingredient i ON i.id = b.id
    ORDER BY b.rank, b.sim DESC, i.name
    LIMIT :limit
    """
)
STANDARD_LIMIT = 5


def _standard_rank(norm: str, candidate: str) -> int | None:
    if candidate == norm:
        return 0
    if candidate.startswith(norm) or f" {norm}" in f" {candidate}":
        return 1
    if norm in candidate:
        return 2
    return None


async def search_ingredients(
    db: AsyncSession,
    q: str,
    *,
    include_standard: bool = False,
    limit: int = 10,
    try_singular: bool = True,
) -> list[IngredientMatch]:
    """Active ingredients whose name or other spelling matches ``q``, best first (1G).

    An exact name or spelling ranks first, then prefixes, then trigram
    similarity; each ingredient appears once, however many spellings match.
    With ``include_standard``, standard-list names the catalog doesn't have yet
    follow. One search serves the picker and the search palette.
    """
    ql = " ".join(q.casefold().split())
    if not ql:
        return []
    # Text that normalizes to nothing ("_", "%") still matches names literally.
    norm = normalize_name(q) or ql
    rows = await db.execute(
        _INGREDIENT_SEARCH_SQL,
        {
            "ql": ql,
            "prefix": f"{like_escape(ql)}%",
            "contains": f"%{like_escape(ql)}%",
            "norm": norm,
            "nprefix": f"{like_escape(norm)}%",
            "ncontains": f"%{like_escape(norm)}%",
            "limit": limit,
        },
    )
    found: list[tuple[int, float, IngredientMatch]] = []
    for r in rows.mappings():
        exact = r["rank"] == 0 or normalize_name(r["name"]) == norm
        found.append(
            (
                0 if exact else r["rank"],
                -float(r["sim"]),
                IngredientMatch(
                    kind="ingredient",
                    id=r["id"],
                    name=r["name"],
                    category=r["category"],
                    canonical_unit=r["canonical_unit"],
                    matched_spelling=r["spelling"],
                    exact=exact,
                ),
            )
        )
    found.sort(key=lambda t: (t[0], t[1], t[2].name.casefold()))
    out = [m for _, _, m in found]
    if include_standard:
        out += await _standard_matches(db, norm)
    if try_singular and not any(m.exact for m in out):
        # "parsnips" names the parsnip: a plural the catalog or the list
        # has no row for still finds its singular, exactly (found dogfooding:
        # the model answers in plurals, and none of its ingredients matched).
        for single in singulars(ql):
            hits = await search_ingredients(
                db, single, include_standard=include_standard, limit=limit, try_singular=False
            )
            exact = [m for m in hits if m.exact]
            if exact:
                seen = {(m.kind, m.id, m.key) for m in exact}
                return exact + [m for m in out if (m.kind, m.id, m.key) not in seen]
    return out


# Longest phrase first: "green onion" before "onion" in "GREEN ONION BNCH".
_LINE_PHRASE_WORDS = 3


@dataclass
class IngredientIndex:
    """Every exact name and spelling, loaded once so a line is read by lookups (#183).

    Answers what ``search_ingredients(..., include_standard=True)`` answers for
    its exact matches, without a query per phrase: the naming list reads
    a hundred lines at once, a dozen phrases each.
    """

    by_name: dict[str, list[Ingredient]] = field(default_factory=dict)
    by_norm: dict[str, list[Ingredient]] = field(default_factory=dict)
    by_spelling: dict[str, list[Ingredient]] = field(default_factory=dict)
    standard_by_norm: dict[str, list[tuple[standard.StandardEntry, str | None]]] = field(
        default_factory=dict
    )
    taken_keys: set[str] = field(default_factory=set)
    taken_names: set[str] = field(default_factory=set)
    by_id: dict[uuid.UUID, Ingredient] = field(default_factory=dict)
    by_slug: dict[str, Ingredient] = field(default_factory=dict)


async def ingredient_index(db: AsyncSession) -> IngredientIndex:
    index = IngredientIndex()
    for ing in (await db.execute(select(Ingredient))).scalars():
        # A standard name is taken by any ingredient, active or not, as in
        # _standard_matches; only active ones are offered.
        index.taken_keys.add(ing.slug)
        index.taken_names.add(ing.name.lower())
        index.by_id[ing.id] = ing
        index.by_slug[ing.slug] = ing
        if ing.active:
            index.by_name.setdefault(ing.name.lower(), []).append(ing)
            index.by_norm.setdefault(normalize_name(ing.name), []).append(ing)
    spellings = await db.execute(
        select(IngredientAlias.name_norm, Ingredient)
        .join(Ingredient, Ingredient.id == IngredientAlias.ingredient_id)
        .where(Ingredient.active.is_(True))
    )
    for name_norm, ing in spellings:
        index.by_spelling.setdefault(name_norm, []).append(ing)
    for e in standard.standard_list().ingredients:
        norms: dict[str, str | None] = {normalize_name(e.name): None}
        for spelling in e.spellings:
            norms.setdefault(normalize_name(spelling), spelling)
        for norm, spelling in norms.items():
            index.standard_by_norm.setdefault(norm, []).append((e, spelling))
    return index


def _exact_in_index(index: IngredientIndex, q: str, *, try_singular: bool = True):
    """The exact matches ``search_ingredients`` would give for ``q``, in its order."""
    ql = " ".join(q.casefold().split())
    if not ql:
        return []
    norm = normalize_name(q) or ql
    # Ingredients: a literal name or a spelling first (similarity 1), then a
    # name equal only once normalized; by name within each.
    hits: dict[uuid.UUID, tuple[int, Ingredient, str | None]] = {}
    for ing in index.by_name.get(ql, []):
        hits[ing.id] = (0, ing, None)
    for ing in index.by_spelling.get(norm, []):
        hits.setdefault(ing.id, (0, ing, norm))
    for ing in index.by_norm.get(norm, []):
        hits.setdefault(ing.id, (1, ing, None))
    ordered = sorted(hits.values(), key=lambda t: (t[0], t[1].name.casefold()))
    out = [
        IngredientMatch(
            kind="ingredient",
            id=ing.id,
            name=ing.name,
            category=ing.category,
            canonical_unit=ing.canonical_unit,
            matched_spelling=spelling,
            exact=True,
        )
        for _, ing, spelling in ordered
    ]
    entries = sorted(
        (
            (e, spelling)
            for e, spelling in index.standard_by_norm.get(norm, [])
            if e.key not in index.taken_keys and e.name.lower() not in index.taken_names
        ),
        key=lambda t: t[0].name.casefold(),
    )
    out += [
        IngredientMatch(
            kind="standard",
            key=e.key,
            name=e.name,
            category=e.category,
            canonical_unit=e.unit,
            matched_spelling=spelling,
            exact=True,
        )
        for e, spelling in entries[:STANDARD_LIMIT]
    ]
    if try_singular and not out:
        for single in singulars(ql):
            found = _exact_in_index(index, single, try_singular=False)
            if found:
                return found
    return out


async def ingredients_in_text(
    db: AsyncSession,
    receipt_text: str,
    *,
    limit: int = 3,
    index: IngredientIndex | None = None,
) -> list[IngredientMatch]:
    """Ingredients whose name or spelling is spelled out in a receipt line (#88).

    Each run of up to three words of the line is looked up, longest first, and
    only an exact name or spelling counts: "WT BROCCOLI CROWNS" offers broccoli,
    "BNLS CHKN BRST" offers nothing. A plural finds its singular, and a pair of
    words is tried in both orders ("SQUASH BUTTERNUT" offers butternut squash). The longest
    phrase comes first ("garlic powder" before "garlic"); at the same length a
    catalog ingredient comes before a standard name the catalog doesn't have
    yet. A suggestion is only ever offered. Pass ``index`` to read many lines
    against one load of the names.
    """
    words = [w for w in normalize_receipt_text(receipt_text).casefold().split() if w.isalpha()]
    if not words:
        return []
    if index is None:
        index = await ingredient_index(db)
    phrases: list[str] = []
    for n in range(min(_LINE_PHRASE_WORDS, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i : i + n])
            if phrase not in phrases:
                phrases.append(phrase)
            # Tills print the noun first ("SQUASH BUTTERNUT", "PEPPERS RED"): a pair
            # is tried the other way round too, still only as an exact name.
            if n == 2 and (swapped := f"{words[i + 1]} {words[i]}") not in phrases:
                phrases.append(swapped)
    # The longest phrase names the most specific thing ("garlic powder" over
    # "garlic"); at the same length a catalog ingredient comes before a
    # standard name the catalog doesn't have yet.
    found: list[tuple[int, int, int, IngredientMatch]] = []
    seen: set[str] = set()
    for order, phrase in enumerate(phrases):
        for match in _exact_in_index(index, phrase):
            ident = f"i:{match.id}" if match.kind == "ingredient" else f"s:{match.key}"
            if ident in seen:
                continue
            seen.add(ident)
            kind = 0 if match.kind == "ingredient" else 1
            found.append((-len(phrase.split()), kind, order, match))
    found.sort(key=lambda t: t[:3])
    return [m for *_, m in found][:limit]


async def _standard_matches(db: AsyncSession, norm: str) -> list[IngredientMatch]:
    """Standard names matching ``norm`` that no ingredient has taken, by slug or name."""
    scored: list[tuple[int, str, standard.StandardEntry, str | None]] = []
    for e in standard.standard_list().ingredients:
        best: tuple[int, str | None] | None = None
        rank = _standard_rank(norm, normalize_name(e.name))
        if rank is not None:
            best = (rank, None)
        for spelling in e.spellings:
            rank = _standard_rank(norm, normalize_name(spelling))
            if rank is not None and (best is None or rank < best[0]):
                best = (rank, spelling)
        if best is not None:
            scored.append((best[0], e.name.casefold(), e, best[1]))
    if not scored:
        return []
    scored.sort(key=lambda t: (t[0], t[1]))
    keys = [e.key for _, _, e, _ in scored]
    names = [e.name.lower() for _, _, e, _ in scored]
    taken_rows = await db.execute(
        select(Ingredient.slug, func.lower(Ingredient.name)).where(
            or_(Ingredient.slug.in_(keys), func.lower(Ingredient.name).in_(names))
        )
    )
    taken_keys: set[str] = set()
    taken_names: set[str] = set()
    for slug, lname in taken_rows:
        taken_keys.add(slug)
        taken_names.add(lname)
    out: list[IngredientMatch] = []
    for rank, _, e, spelling in scored:
        if e.key in taken_keys or e.name.lower() in taken_names:
            continue
        out.append(
            IngredientMatch(
                kind="standard",
                key=e.key,
                name=e.name,
                category=e.category,
                canonical_unit=e.unit,
                matched_spelling=spelling,
                exact=rank == 0,
            )
        )
        if len(out) == STANDARD_LIMIT:
            break
    return out


async def new_ingredient(db: AsyncSession, spec: IngredientCreate) -> Ingredient:
    """Add and flush an ingredient with its generated spellings; the caller commits.

    With ``standard_key``, the standard-list entry supplies the name, category
    and unit, and its spellings, USDA reference and measures are added too; the
    entry's key becomes the slug and the ingredient starts ``linked`` (03, 1G).
    Raises ``IntegrityError`` for a taken name, like a plain create.
    """
    if spec.standard_key is None:
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
        await add_generated_spellings(db, ingredient)
        return ingredient
    entry = standard.entry(spec.standard_key)
    if entry is None:
        raise ApiError(
            422, "unknown_standard_entry", "The standard list has no entry with that key."
        )
    ingredient = Ingredient(
        name=entry.name,
        slug=entry.key,
        reconcile_state="linked",
        category=entry.category,
        canonical_unit=entry.unit,
        yield_pct=spec.yield_pct,
        perishability=spec.perishability,
        notes=spec.notes,
    )
    db.add(ingredient)
    await db.flush()
    await apply_standard_entry(db, ingredient, entry)
    return ingredient


async def apply_standard_entry(
    db: AsyncSession, ingredient: Ingredient, entry: standard.StandardEntry
) -> None:
    """Give an ingredient a standard entry's spellings, USDA reference and new measures.

    Spellings another ingredient already has are skipped, as generated ones are:
    the list is a suggestion, and the ingredient being saved matters more.
    """
    await add_generated_spellings(db, ingredient)
    for spelling in entry.spellings:
        try:
            async with db.begin_nested():
                await add_spelling(db, ingredient.id, spelling, kind="synonym", source="standard")
        except ApiError:
            log.info(
                "skipped a standard spelling another ingredient has",
                extra={"ingredient_id": str(ingredient.id), "spelling": spelling},
            )
    if entry.fdc is not None:
        await set_preferred_fdc(db, ingredient.id, entry.fdc)
    have = {
        label.lower()
        for label in (
            await db.execute(
                select(IngredientMeasure.label).where(
                    IngredientMeasure.ingredient_id == ingredient.id
                )
            )
        ).scalars()
    }
    for measure in entry.measures:
        if measure.label.lower() not in have:
            db.add(
                IngredientMeasure(
                    ingredient_id=ingredient.id,
                    label=measure.label,
                    canonical_qty=measure.qty,
                    source="manual",
                    confirmed=False,
                )
            )
    await db.flush()


async def set_preferred_fdc(db: AsyncSession, ingredient_id: uuid.UUID, fdc_id: int) -> None:
    """Make ``fdc_id`` the ingredient's preferred USDA reference, keeping any others."""
    refs = (
        (
            await db.execute(
                select(IngredientRef).where(
                    IngredientRef.ingredient_id == ingredient_id, IngredientRef.system == "fdc"
                )
            )
        )
        .scalars()
        .all()
    )
    for ref in refs:
        if ref.is_preferred and ref.external_id != str(fdc_id):
            ref.is_preferred = False
    await db.flush()
    match = next((r for r in refs if r.external_id == str(fdc_id)), None)
    if match is None:
        db.add(
            IngredientRef(
                ingredient_id=ingredient_id,
                system="fdc",
                external_id=str(fdc_id),
                is_preferred=True,
            )
        )
    else:
        match.is_preferred = True
    await db.flush()


async def create_ingredient(db: AsyncSession, payload: IngredientCreate) -> Ingredient:
    try:
        ingredient = await new_ingredient(db, payload)
    except IntegrityError as exc:
        await db.rollback()
        raise _ingredient_conflict(exc) from exc
    except ApiError:
        await db.rollback()
        raise
    await db.commit()
    return await get_ingredient(db, ingredient.id)


def _ingredient_conflict(exc: IntegrityError) -> ApiError:
    if "uq_ingredient_name_lower" in str(exc.orig):
        return ApiError(409, "ingredient_name_taken", "An ingredient with that name exists.")
    if "uq_ingredient_slug" in str(exc.orig):
        return ApiError(
            409, "ingredient_name_taken", "That standard name is already in your catalog."
        )
    return ApiError(409, "conflict", "The ingredient could not be saved.")


async def update_ingredient(
    db: AsyncSession, ingredient_id: uuid.UUID, payload: IngredientUpdate
) -> Ingredient:
    ingredient = await get_ingredient(db, ingredient_id)
    old_unit = ingredient.canonical_unit
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
    set_fields = set(payload.model_dump(exclude_unset=True))
    bridge_changed = bool(
        {"density_g_per_ml", "density_source", "clear_density", "canonical_unit"} & set_fields
    )
    unit_changed = "canonical_unit" in set_fields and ingredient.canonical_unit != old_unit
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _ingredient_conflict(exc) from exc
    if bridge_changed:
        await _after_ingredient_bridge_change(
            db, ingredient_id, canonical_unit_changed=unit_changed
        )
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
    return select(Product).options(
        selectinload(Product.ingredient), selectinload(Product.identifiers)
    )


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
    category: CategoryKey | Literal["none"] | None = None,
    barcode: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[ProductListItem], str | None]:
    """Products by name, a page at a time; with ``q``, the best ``limit`` matches by rank.

    Both filter on the server, so a search or a category finds products beyond the
    first page (D12). A ranked search has no next page, like the typeahead. A
    ``barcode`` is an exact match and ignores the other filters. ``category="none"``
    is the products whose ingredient has no category key: none at all, or free
    text the synonym map doesn't know (they show no chip).
    """
    uncategorized = category == "none"
    category_values = await _category_values(db, category) if category else None
    if barcode:
        rows = await _products_by_barcode(db, barcode, limit)
        next_cursor = None
    elif category_values == [] and not uncategorized:
        return [], None
    elif q:
        hits = await search_products(
            db,
            q,
            limit,
            include_inactive=include_inactive,
            ingredient_id=ingredient_id,
            category_values=category_values,
            uncategorized=uncategorized,
        )
        rows, next_cursor = await _products_by_id(db, [h.id for h in hits]), None
    else:
        rows, next_cursor = await _product_page(
            db,
            ingredient_id=ingredient_id,
            include_inactive=include_inactive,
            category_values=category_values,
            uncategorized=uncategorized,
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
    uncategorized: bool = False,
) -> tuple[list[Product], str | None]:
    # Keyset on (lower(name), id), with lower(name) as Postgres computes it, so
    # the cursor compares exactly the way the ORDER BY sorts.
    sort_name = func.lower(Product.name)
    stmt = _product_query().add_columns(sort_name).order_by(sort_name, Product.id).limit(limit + 1)
    if not include_inactive:
        stmt = stmt.where(Product.active.is_(True))
    if ingredient_id is not None:
        stmt = stmt.where(Product.ingredient_id == ingredient_id)
    if uncategorized:
        stmt = stmt.join(Product.ingredient).where(
            Ingredient.category.is_(None) | Ingredient.category.in_(category_values or [])
        )
    elif category_values is not None:
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


def _identifier_keys(code: str) -> list[str]:
    """ "scheme:value" keys a typed or scanned code may be stored under (never raises)."""
    return [f"{scheme}:{value}" for scheme, value in lookup_keys(code)]


async def _products_by_barcode(db: AsyncSession, code: str, limit: int) -> list[Product]:
    """Active products whose GTIN or other code is ``code``, in any of its written forms."""
    keys = _identifier_keys(code)
    if not keys:
        return []
    ids = (
        await db.execute(
            select(Product.id)
            .join(Ingredient, Ingredient.id == Product.ingredient_id)
            .join(ProductIdentifier, ProductIdentifier.product_id == Product.id)
            .where(
                Product.active,
                Ingredient.active,
                ProductIdentifier.vendor_id.is_(None),
                (ProductIdentifier.scheme + ":" + ProductIdentifier.value).in_(keys),
            )
            .order_by(Product.name, Product.id)
            .limit(limit)
        )
    ).scalars()
    return await _products_by_id(db, list(dict.fromkeys(ids)))


async def _category_values(db: AsyncSession, key: CategoryKey | Literal["none"]) -> list[str]:
    """The stored free-text categories that map to ``key`` ("none": to no key).

    Filtering on these exact values keeps the synonym map in one place,
    ``app/catalog/categories.py``, instead of a second copy in SQL.
    """
    stored = await db.execute(
        select(Ingredient.category).where(Ingredient.category.is_not(None)).distinct()
    )
    want = None if key == "none" else key
    return [c for c in stored.scalars() if categories.key(c) == want]


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
    if "uq_product_identifier_value" in message:
        return ApiError(409, "barcode_taken", "Another product already has that barcode.")
    if "uq_ingredient_name_lower" in message:
        return ApiError(409, "ingredient_name_taken", "An ingredient with that name exists.")
    if "uq_ingredient_slug" in message:
        return ApiError(
            409, "ingredient_name_taken", "That standard name is already in your catalog."
        )
    if "pack_unit" in message or "unit.code" in message:
        return ApiError(422, "unknown_unit", "pack_unit is not a known unit code.")
    return ApiError(409, "conflict", "The product could not be saved.")


def _barcode_code(raw: str, symbology: str | None) -> tuple[str, str]:
    try:
        return classify_barcode(raw, symbology)  # type: ignore[arg-type]
    except AmbiguousCode as exc:
        raise ApiError(
            422,
            "ambiguous_barcode",
            "These eight digits are a valid EAN-8 and a valid UPC-E; "
            "say which in barcode_symbology.",
        ) from exc
    except InvalidGtin as exc:
        raise ApiError(422, "invalid_gtin", "That barcode's check digit is wrong.") from exc


async def _set_barcode(
    db: AsyncSession, product: Product, raw: str | None, symbology: str | None
) -> None:
    """Write the API's barcode field: replace the product's barcode identifier (03, 1H)."""
    current = product.barcode_identifier
    if raw is None:
        if current is not None:
            product.identifiers.remove(current)
        return
    scheme, value = _barcode_code(raw, symbology)
    if current is not None and (current.scheme, current.value) == (scheme, value):
        return
    taken = (
        await db.execute(
            select(ProductIdentifier.product_id).where(
                ProductIdentifier.scheme == scheme,
                ProductIdentifier.value == value,
                ProductIdentifier.vendor_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if taken is not None and taken != product.id:
        raise ApiError(409, "barcode_taken", "Another product already has that barcode.")
    if current is not None:
        product.identifiers.remove(current)
        await db.flush()
    product.identifiers.append(ProductIdentifier(scheme=scheme, value=value, source="manual"))


def _kind_from_evidence(
    brand: str | None, barcode: str | None, exclusive_vendor_id: uuid.UUID | None
) -> str:
    """The kind a product starts with when none is given (03, 1H)."""
    if exclusive_vendor_id is not None:
        return "unbranded_vendor"
    if (brand or "").strip() or barcode:
        return "branded"
    return "loose"


def _attributes(ingredient: Ingredient, data: dict | None) -> dict:
    try:
        return validate_attributes(categories.key(ingredient.category), data)
    except ValueError as exc:
        raise ApiError(422, "invalid_attributes", str(exc).splitlines()[0]) from exc


async def create_product(db: AsyncSession, payload: ProductCreate) -> Product:
    """Create a product, and its ingredient inline if asked, in one transaction."""
    try:
        if payload.ingredient is not None:
            ingredient = await new_ingredient(db, payload.ingredient)
            ingredient_id = ingredient.id
        else:
            assert payload.ingredient_id is not None
            ingredient = await db.get(Ingredient, payload.ingredient_id)
            if ingredient is None:
                raise ApiError(404, "not_found", "No such ingredient.")
            ingredient_id = payload.ingredient_id
        product = Product(
            ingredient_id=ingredient_id,
            brand=payload.brand,
            name=payload.name.strip(),
            pack_qty=payload.pack_qty,
            pack_unit=payload.pack_unit,
            pack_count=payload.pack_count,
            piece_name=_piece_name(payload.piece_name),
            kind=payload.kind
            or _kind_from_evidence(payload.brand, payload.barcode, payload.exclusive_vendor_id),
            attributes=_attributes(ingredient, payload.attributes),
            quality_rating=payload.quality_rating,
            exclusive_vendor_id=payload.exclusive_vendor_id,
            density_override=payload.density_override,
            density_override_source=payload.density_override_source,
            density_override_confirmed=False,
            notes=payload.notes,
        )
        product.identifiers = []
        await _check_pieces(db, product)
        db.add(product)
        await _set_barcode(db, product, payload.barcode, payload.barcode_symbology)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _product_conflict(exc) from exc
    except ApiError:
        await db.rollback()
        raise
    return await get_product(db, product.id)


def _piece_name(name: str | None) -> str | None:
    """A piece's name as typed, trimmed and lower-cased ("Link" -> "link"); blank is none."""
    return (name.strip().lower() or None) if name else None


async def _check_pieces(db: AsyncSession, product: Product) -> None:
    """Pieces belong to a mass or volume pack: a count pack already says how many."""
    if product.pack_count is None:
        return
    with db.no_autoflush:  # the change is checked before it is written
        units = await load_units(db)
    unit = units.get(product.pack_unit or "")
    if product.pack_qty is None or unit is None or unit.dimension == "count":
        raise ApiError(
            422,
            "pieces_need_size",
            "Pieces go with a pack's weight or volume; "
            "a pack counted in pieces already says how many.",
        )


async def update_product(
    db: AsyncSession, product_id: uuid.UUID, payload: ProductUpdate
) -> Product:
    product = await get_product(db, product_id)
    data = payload.model_dump(exclude_unset=True)
    if data.pop("clear_pack", False):
        product.pack_qty = None
        product.pack_unit = None
        product.pack_count = None
        product.piece_name = None
    if data.get("pack_qty") is not None:
        product.pack_qty = data.pop("pack_qty")
        product.pack_unit = data.pop("pack_unit")
    data.pop("pack_qty", None)
    data.pop("pack_unit", None)
    if data.pop("clear_pieces", False):
        product.pack_count = None
        product.piece_name = None
    if data.get("pack_count") is not None:
        product.pack_count = data.pop("pack_count")
        product.piece_name = _piece_name(data.pop("piece_name", None))
    elif data.get("piece_name") is not None:
        if product.pack_count is None:
            await db.rollback()
            raise ApiError(422, "pieces_need_count", "Say how many pieces before naming one.")
        product.piece_name = _piece_name(data.pop("piece_name"))
    data.pop("pack_count", None)
    data.pop("piece_name", None)
    try:
        await _check_pieces(db, product)
    except ApiError:
        await db.rollback()
        raise
    symbology = data.pop("barcode_symbology", None)
    if data.pop("clear_barcode", False):
        await _set_barcode(db, product, None, None)
    if data.get("barcode") is not None:
        try:
            await _set_barcode(db, product, data["barcode"], symbology)
        except ApiError:
            await db.rollback()
            raise
    data.pop("barcode", None)
    if "attributes" in data:
        ingredient = await db.get(Ingredient, data.get("ingredient_id") or product.ingredient_id)
        assert ingredient is not None
        data["attributes"] = _attributes(ingredient, data["attributes"])
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
        if value is None and key in {"name", "kind"}:
            continue
        setattr(product, key, value)
    bridge_changed = bool(
        {
            "pack_qty",
            "pack_unit",
            "clear_pack",
            "pack_count",
            "clear_pieces",
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
    SELECT p.id, p.name, p.brand, bc.scheme AS barcode_scheme, bc.value AS barcode_value,
           p.pack_qty, p.pack_unit, p.pack_count, p.piece_name, p.quality_rating,
           i.id AS ingredient_id, i.name AS ingredient_name, i.canonical_unit,
           i.active AS ingredient_active, i.category,
           EXISTS (
               SELECT 1 FROM product_identifier pi
               WHERE pi.product_id = p.id AND pi.vendor_id IS NULL
                 AND pi.scheme || ':' || pi.value = ANY(CAST(:codes AS text[]))
           ) AS barcode_hit,
           word_similarity(:ql, lower(p.name)) AS s_name,
           word_similarity(:ql, lower(coalesce(p.brand, ''))) AS s_brand,
           word_similarity(:ql, lower(i.name)) AS s_ingredient
    FROM product p
    JOIN ingredient i ON i.id = p.ingredient_id
    LEFT JOIN LATERAL (
        SELECT scheme, value FROM product_identifier
        WHERE product_id = p.id AND scheme IN ('gtin', 'other')
        ORDER BY created_at, id LIMIT 1
    ) bc ON true
    WHERE {filters} AND (
        EXISTS (
            SELECT 1 FROM product_identifier pi
            WHERE pi.product_id = p.id AND pi.vendor_id IS NULL
              AND pi.scheme || ':' || pi.value = ANY(CAST(:codes AS text[]))
        )
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
    uncategorized: bool = False,
) -> list[SearchHit]:
    ql = q.strip().lower()
    if not ql:
        return []
    params: dict[str, object] = {
        "codes": _identifier_keys(q),
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
    if uncategorized:
        filters.append("(i.category IS NULL OR i.category = ANY(CAST(:category_values AS text[])))")
        params["category_values"] = category_values or []
    elif category_values is not None:
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
                barcode=display(r["barcode_scheme"], r["barcode_value"])
                if r["barcode_scheme"]
                else None,
                pack_qty=r["pack_qty"],
                pack_unit=r["pack_unit"],
                pack_count=r["pack_count"],
                piece_name=r["piece_name"],
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


async def _after_ingredient_bridge_change(
    db: AsyncSession, ingredient_id: uuid.UUID, *, canonical_unit_changed: bool = False
) -> None:
    await recompute_for_ingredient(db, ingredient_id, canonical_unit_changed=canonical_unit_changed)


async def _after_product_bridge_change(db: AsyncSession, product_id: uuid.UUID) -> None:
    await recompute_for_product(db, product_id)
