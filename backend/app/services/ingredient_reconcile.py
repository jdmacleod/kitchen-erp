"""Linking existing ingredients to the standard list, and merging duplicates (03, 1G).

Ingredients that existed before 1G start ``unreviewed``. The link page offers
each one its likely standard name; a person links, renames or skips it. When a
link or rename would give it a name another active ingredient already has, the
two are merged instead, and the person picks which one survives.

A merge is one transaction with a single commit:

1. The loser is renamed "<name> (merged into <target>)" and its slug freed,
   then flushed: the unique name index covers inactive rows.
2. The survivor takes the target name (and the standard key), then is flushed.
3. Products move to the survivor; the loser's spellings and references follow,
   and both old names become ``legacy`` spellings.
4. The measures the person ticked are copied, when the units agree.
5. The loser is deactivated with ``merged_into`` set.
6. Every price of the survivor is renormalized in full through the flush-only
   core, as a change of canonical unit requires (#102, O1, O2).

A preview runs the same merge and rolls it back, so the number of prices that
would need a bridge is counted, not estimated.
"""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import standard
from app.catalog.names import GENERATED_SLUG_PREFIX, STANDARD_KEY_RE, normalize_name, plural
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.models.catalog import (
    FdcFood,
    Ingredient,
    IngredientAlias,
    IngredientMeasure,
    IngredientRef,
    Product,
)
from app.models.purchases import PriceNorm, PriceObservation
from app.services import pricebook
from app.services.catalog import apply_standard_entry
from app.services.spellings import add_generated_spellings, add_spelling

log = get_logger(__name__)

_CLOSE_MATCH = 0.8


# --- suggestions -------------------------------------------------------------


def _entry_index() -> dict[str, standard.StandardEntry]:
    index: dict[str, standard.StandardEntry] = {}
    for e in standard.standard_list().ingredients:
        forms = [e.name, *e.spellings]
        if (p := plural(e.name)) is not None:
            forms.append(p)
        for form in forms:
            index.setdefault(normalize_name(form), e)
    return index


def suggest_entry(name: str) -> standard.StandardEntry | None:
    """The standard entry an ingredient name most likely means, or None.

    An exact name, spelling or plural wins; otherwise the closest name above a
    similarity bar. A suggestion is only ever offered, never applied.
    """
    norm = normalize_name(name)
    index = _entry_index()
    if norm in index:
        return index[norm]
    close = difflib.get_close_matches(norm, list(index), n=1, cutoff=_CLOSE_MATCH)
    return index[close[0]] if close else None


@dataclass
class Other:
    id: uuid.UUID
    name: str
    canonical_unit: str
    products: int


@dataclass
class LinkRow:
    id: uuid.UUID
    name: str
    canonical_unit: str
    category: str | None
    products: int
    reconcile_state: str
    suggestion: standard.StandardEntry | None = None
    usda_description: str | None = None
    # Another active ingredient already named like the suggestion: a merge.
    conflict: Other | None = None


async def _product_counts(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Product.ingredient_id, func.count())
        .where(Product.ingredient_id.in_(ids), Product.active)
        .group_by(Product.ingredient_id)
    )
    return {r[0]: r[1] for r in rows}


async def holder_of(
    db: AsyncSession, name: str, *, key: str | None = None, exclude: uuid.UUID
) -> Ingredient | None:
    """An active ingredient other than ``exclude`` that has this name, key or spelling."""
    norm = normalize_name(name)
    conditions = [func.lower(Ingredient.name) == name.strip().lower()]
    if key is not None:
        conditions.append(Ingredient.slug == key)
    alias_owner = select(IngredientAlias.ingredient_id).where(IngredientAlias.name_norm == norm)
    conditions.append(Ingredient.id.in_(alias_owner))
    stmt = (
        select(Ingredient)
        .where(Ingredient.active, Ingredient.id != exclude, or_(*conditions))
        .order_by(Ingredient.created_at)
    )
    return (await db.execute(stmt)).scalars().first()


async def link_rows(db: AsyncSession) -> tuple[list[LinkRow], list[LinkRow]]:
    """(to review, skipped): active ingredients awaiting a decision, likely duplicates first."""
    ingredients = (
        (
            await db.execute(
                select(Ingredient)
                .where(Ingredient.active, Ingredient.reconcile_state.in_(("unreviewed", "skipped")))
                .order_by(Ingredient.name)
            )
        )
        .scalars()
        .all()
    )
    counts = await _product_counts(db, [i.id for i in ingredients])
    rows: list[LinkRow] = []
    for i in ingredients:
        row = LinkRow(
            id=i.id,
            name=i.name,
            canonical_unit=i.canonical_unit,
            category=i.category,
            products=counts.get(i.id, 0),
            reconcile_state=i.reconcile_state,
            suggestion=suggest_entry(i.name),
        )
        if row.suggestion is not None:
            other = await holder_of(db, row.suggestion.name, key=row.suggestion.key, exclude=i.id)
            if other is not None:
                row.conflict = Other(
                    other.id,
                    other.name,
                    other.canonical_unit,
                    (await _product_counts(db, [other.id])).get(other.id, 0),
                )
        rows.append(row)
    fdc_ids = [r.suggestion.fdc for r in rows if r.suggestion and r.suggestion.fdc]
    if fdc_ids:
        found = await db.execute(
            select(FdcFood.fdc_id, FdcFood.description).where(FdcFood.fdc_id.in_(fdc_ids))
        )
        descriptions = dict(found.tuples().all())
        for r in rows:
            if r.suggestion and r.suggestion.fdc:
                r.usda_description = descriptions.get(r.suggestion.fdc)
    shared: dict[str, int] = {}
    for r in rows:
        if r.suggestion:
            shared[r.suggestion.key] = shared.get(r.suggestion.key, 0) + 1

    def duplicate(r: LinkRow) -> bool:
        return r.conflict is not None or (r.suggestion is not None and shared[r.suggestion.key] > 1)

    to_review = sorted(
        (r for r in rows if r.reconcile_state == "unreviewed"),
        key=lambda r: (not duplicate(r), r.name.casefold()),
    )
    skipped = [r for r in rows if r.reconcile_state == "skipped"]
    return to_review, skipped


async def summary(db: AsyncSession) -> dict[str, int]:
    rows = await db.execute(
        select(Ingredient.reconcile_state, func.count())
        .where(Ingredient.active, Ingredient.reconcile_state.in_(("unreviewed", "skipped")))
        .group_by(Ingredient.reconcile_state)
    )
    counts = dict(rows.tuples().all())
    return {"to_review": counts.get("unreviewed", 0), "skipped": counts.get("skipped", 0)}


# --- decisions ---------------------------------------------------------------


async def _ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient:
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None or not ingredient.active:
        raise ApiError(404, "not_found", "No such active ingredient.")
    return ingredient


def _unknown_key() -> ApiError:
    return ApiError(422, "unknown_standard_entry", "The standard list has no entry with that key.")


def _merge_needed(target: str, other: Ingredient, products: int) -> ApiError:
    return ApiError(
        409,
        "merge_needed",
        f"{other.name} already exists. Merge into it?",
        details={
            "target_name": target,
            "other": {
                "id": str(other.id),
                "name": other.name,
                "canonical_unit": other.canonical_unit,
                "products": products,
            },
        },
    )


async def _add_legacy(db: AsyncSession, ingredient_id: uuid.UUID, name: str, source: str) -> None:
    """Keep a former name as a spelling; skipped when another ingredient has it."""
    try:
        async with db.begin_nested():
            await add_spelling(db, ingredient_id, name, kind="legacy", source=source)
    except ApiError:
        # Another ingredient already has this spelling. The rename or merge still
        # goes ahead: the old name just isn't kept as a spelling of this one.
        log.info(
            "skipped a former name another ingredient has",
            extra={"ingredient_id": str(ingredient_id), "spelling": name},
        )


async def _drop_own_spelling(db: AsyncSession, ingredient_id: uuid.UUID, name: str) -> None:
    """A name is never also a spelling of its own ingredient."""
    await db.execute(
        delete(IngredientAlias).where(
            IngredientAlias.ingredient_id == ingredient_id,
            IngredientAlias.name_norm == normalize_name(name),
        )
    )


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_ingredient_name_lower" in str(exc.orig):
            raise ApiError(
                409, "ingredient_name_taken", "An inactive ingredient already has that name."
            ) from exc
        raise


async def link(db: AsyncSession, ingredient_id: uuid.UUID, key: str) -> Ingredient:
    """Give an ingredient a standard name, key, spellings and USDA reference."""
    entry = standard.entry(key)
    if entry is None:
        raise _unknown_key()
    ingredient = await _ingredient(db, ingredient_id)
    other = await holder_of(db, entry.name, key=entry.key, exclude=ingredient.id)
    if other is not None:
        products = (await _product_counts(db, [other.id])).get(other.id, 0)
        raise _merge_needed(entry.name, other, products)
    old = ingredient.name
    ingredient.name = entry.name
    ingredient.slug = entry.key
    ingredient.reconcile_state = "linked"
    if ingredient.category is None:
        ingredient.category = entry.category
    await db.flush()
    await _drop_own_spelling(db, ingredient.id, entry.name)
    if normalize_name(old) != normalize_name(entry.name):
        await _add_legacy(db, ingredient.id, old, "rename")
    await apply_standard_entry(db, ingredient, entry)
    await _commit(db)
    return ingredient


async def rename(db: AsyncSession, ingredient_id: uuid.UUID, name: str) -> Ingredient:
    """Give an ingredient a name a person typed; the old one stays as a spelling."""
    name = name.strip()
    if not normalize_name(name):
        raise ApiError(422, "empty_name", "A name needs at least one letter or digit.")
    ingredient = await _ingredient(db, ingredient_id)
    other = await holder_of(db, name, exclude=ingredient.id)
    if other is not None:
        products = (await _product_counts(db, [other.id])).get(other.id, 0)
        raise _merge_needed(name, other, products)
    old = ingredient.name
    ingredient.name = name
    if ingredient.reconcile_state in ("unreviewed", "skipped"):
        ingredient.reconcile_state = "not_applicable"
    await db.flush()
    await _drop_own_spelling(db, ingredient.id, name)
    if normalize_name(old) != normalize_name(name):
        await _add_legacy(db, ingredient.id, old, "rename")
    await add_generated_spellings(db, ingredient)
    await _commit(db)
    return ingredient


async def set_skipped(db: AsyncSession, ingredient_id: uuid.UUID, skipped: bool) -> Ingredient:
    """Skip leaves an ingredient as it is; reopen puts it back to review."""
    ingredient = await _ingredient(db, ingredient_id)
    ingredient.reconcile_state = "skipped" if skipped else "unreviewed"
    await db.commit()
    return ingredient


# --- merge -------------------------------------------------------------------


@dataclass
class MergeMeasure:
    label: str
    canonical_qty: Decimal
    copyable: bool
    # Pre-ticked when it can be copied and the survivor lacks it (O10).
    suggested: bool


@dataclass
class MergePreview:
    survivor_id: uuid.UUID
    loser_id: uuid.UUID
    target_name: str
    products_moving: int
    unit_from: str
    unit_to: str
    prices_needing_bridge: int
    measures: list[MergeMeasure] = field(default_factory=list)


async def _failing(db: AsyncSession, ingredient_ids: list[uuid.UUID]) -> int:
    stmt = (
        select(func.count())
        .select_from(PriceNorm)
        .join(PriceObservation, PriceObservation.id == PriceNorm.observation_id)
        .join(Product, Product.id == PriceObservation.product_id)
        .where(Product.ingredient_id.in_(ingredient_ids), PriceNorm.status != "ok")
    )
    return int((await db.execute(stmt)).scalar_one())


def _target(key: str | None, name: str | None) -> tuple[str, standard.StandardEntry | None]:
    if key is not None:
        entry = standard.entry(key)
        if entry is None:
            raise _unknown_key()
        return entry.name, entry
    if name is None or not normalize_name(name):
        raise ApiError(422, "merge_target", "Name the standard entry or the new name.")
    return name.strip(), None


async def _merge_in_session(
    db: AsyncSession,
    survivor_id: uuid.UUID,
    loser_id: uuid.UUID,
    *,
    key: str | None,
    name: str | None,
    copy_measures: list[str],
) -> MergePreview:
    """The merge itself, flushed and uncommitted. Returns what it did."""
    if survivor_id == loser_id:
        raise ApiError(422, "merge_self", "An ingredient can't be merged into itself.")
    target_name, entry = _target(key, name)
    survivor = await _ingredient(db, survivor_id)
    loser = await _ingredient(db, loser_id)
    before = await _failing(db, [survivor.id, loser.id])
    loser_name, survivor_name, loser_slug = loser.name, survivor.name, loser.slug
    unit_from, unit_to = loser.canonical_unit, survivor.canonical_unit
    measure_of = IngredientMeasure.ingredient_id
    measures = (
        (await db.execute(select(IngredientMeasure).where(measure_of == loser.id))).scalars().all()
    )
    have = {
        label.lower()
        for label in (
            await db.execute(select(IngredientMeasure.label).where(measure_of == survivor.id))
        ).scalars()
    }
    same_unit = unit_from == unit_to
    offered = [
        MergeMeasure(
            label=m.label,
            canonical_qty=m.canonical_qty,
            copyable=same_unit and m.label.lower() not in have,
            suggested=same_unit and m.label.lower() not in have,
        )
        for m in measures
    ]
    products_moving = (await _product_counts(db, [loser.id])).get(loser.id, 0)

    # 1. Retire the loser's name and slug first: the unique indexes cover inactive rows.
    loser.name = f"{loser_name} (merged into {target_name})"[:200]
    loser.slug = f"{GENERATED_SLUG_PREFIX}merged-{loser.id}"
    await db.flush()
    # 2. The survivor takes the target name, and a standard key when there is one.
    survivor.name = target_name
    if entry is not None:
        survivor.slug = entry.key
    elif STANDARD_KEY_RE.fullmatch(loser_slug) and not STANDARD_KEY_RE.fullmatch(survivor.slug):
        survivor.slug = loser_slug
    if survivor.category is None:
        survivor.category = entry.category if entry is not None else loser.category
    linked = entry is not None or "linked" in (survivor.reconcile_state, loser.reconcile_state)
    survivor.reconcile_state = "linked" if linked else "not_applicable"
    await db.flush()
    # 3. Products, spellings and references move; old names stay findable.
    await db.execute(
        update(Product).where(Product.ingredient_id == loser.id).values(ingredient_id=survivor.id)
    )
    await db.execute(
        update(IngredientAlias)
        .where(IngredientAlias.ingredient_id == loser.id)
        .values(ingredient_id=survivor.id)
    )
    await _drop_own_spelling(db, survivor.id, target_name)
    ref_of = IngredientRef.ingredient_id
    survivor_refs = {
        (r.system, r.external_id): r
        for r in (await db.execute(select(IngredientRef).where(ref_of == survivor.id))).scalars()
    }
    preferred = {system for (system, _), r in survivor_refs.items() if r.is_preferred}
    for ref in (await db.execute(select(IngredientRef).where(ref_of == loser.id))).scalars().all():
        if (ref.system, ref.external_id) in survivor_refs:
            await db.delete(ref)
            continue
        if ref.is_preferred and ref.system in preferred:
            ref.is_preferred = False
        elif ref.is_preferred:
            preferred.add(ref.system)
        ref.ingredient_id = survivor.id
    await db.flush()
    for old, source in ((loser_name, "merge"), (survivor_name, "rename")):
        if normalize_name(old) != normalize_name(target_name):
            await _add_legacy(db, survivor.id, old, source)
    # 4. The measures the person ticked, when the units agree.
    wanted = {label.lower() for label in copy_measures}
    for m in measures:
        if same_unit and m.label.lower() in wanted and m.label.lower() not in have:
            db.add(
                IngredientMeasure(
                    ingredient_id=survivor.id,
                    label=m.label,
                    canonical_qty=m.canonical_qty,
                    source=m.source,
                    confirmed=m.confirmed,
                )
            )
    await db.flush()
    if entry is not None:
        await apply_standard_entry(db, survivor, entry)
    else:
        await add_generated_spellings(db, survivor)
    # 5. The loser leaves.
    loser.active = False
    loser.merged_into = survivor.id
    loser.reconcile_state = "not_applicable"
    await db.flush()
    # 6. Every price of the survivor, renormalized in this transaction.
    db.expire_all()
    await pricebook.recompute_for_ingredient(
        db, survivor_id, canonical_unit_changed=True, commit=False
    )
    after = await _failing(db, [survivor_id])
    return MergePreview(
        survivor_id=survivor_id,
        loser_id=loser_id,
        target_name=target_name,
        products_moving=products_moving,
        unit_from=unit_from,
        unit_to=unit_to,
        prices_needing_bridge=max(after - before, 0),
        measures=offered,
    )


async def merge_preview(
    db: AsyncSession,
    survivor_id: uuid.UUID,
    loser_id: uuid.UUID,
    *,
    key: str | None = None,
    name: str | None = None,
) -> MergePreview:
    """What a merge would do, from a trial merge that is rolled back."""
    try:
        return await _merge_in_session(
            db, survivor_id, loser_id, key=key, name=name, copy_measures=[]
        )
    finally:
        await db.rollback()


async def merge(
    db: AsyncSession,
    survivor_id: uuid.UUID,
    loser_id: uuid.UUID,
    *,
    key: str | None = None,
    name: str | None = None,
    copy_measures: list[str] | None = None,
) -> MergePreview:
    try:
        done = await _merge_in_session(
            db, survivor_id, loser_id, key=key, name=name, copy_measures=copy_measures or []
        )
    except ApiError:
        await db.rollback()
        raise
    await _commit(db)
    return done
