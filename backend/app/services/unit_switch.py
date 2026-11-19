"""Liquids measured in g, and changing an ingredient's unit safely (issue 358).

A new ingredient used to be measured in g whatever it was. Juices, wines and
syrups bought by volume then sat on Needs a bridge as "no density" until
someone found one. ``volume_only`` finds the ingredients whose every product
with a pack is sold by volume, so their bridge row can offer "Measure in ml
instead". The change itself is the ordinary unit change on the ingredient,
which recomputes its prices.

A named measure is stored in the ingredient's unit ("1 bottle = 750 g"), and
with no density it can't be converted, so a unit change would leave it saying
750 ml. ``guard_unit_change`` refuses that unless the request drops the
measures. This is the same for every caller, the ingredient page included.

Unit drift (#527): an ingredient linked to a standard entry whose unit is not
the entry's ("the standard list measures this in ml; this ingredient is in g").
``unit_mismatches`` finds them for Tidy the catalog (``unit_mismatch``) and
works out what a switch would do: the measures that still convert are kept and
rewritten in the new unit, the rest dropped; the entry's missing measures are
added; and it names the prices that would stop normalizing, the recipe lines
priced only from them, and the lines that could no longer convert. A switch is
``sure`` only when it drops nothing and leaves every line priced and
convertible. ``accept`` applies one in the caller's transaction: the guard, the
rewrite, the entry's measures, the unit change, the renormalization and the
recompute, all or nothing.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog import standard
from app.core.errors import ApiError
from app.models import (
    PriceNorm,
    PriceObservation,
    PriceObservationVoid,
    Recipe,
    RecipeCostLine,
    RecipeCostSnapshot,
    RecipeIngredient,
)
from app.models.catalog import Ingredient, IngredientMeasure, Product
from app.schemas.catalog import IngredientUpdate
from app.services import catalog
from app.services.cleanup_suggestion import Consequence, Suggestion, fingerprint, suggestion_key
from app.services.recipe_cost_lines import from_unit_of
from app.services.units import build_context
from app.units import ConversionContext, ConversionFailure, Measure, convert, is_volume

# A measure a person entered (#527, M4): the standard list's own come back as added.
BY_HAND = frozenset({"manual", "measured", "label"})
_PLACES = Decimal("0.0001")


async def volume_only(db: AsyncSession, ingredient_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """Of these ingredients, the ones in g whose every active product with a pack
    is sold by volume (and that have at least one), in one query."""
    ids = set(ingredient_ids)
    if not ids:
        return set()
    rows = await db.execute(
        select(Product.ingredient_id, Product.pack_unit)
        .join(Ingredient, Ingredient.id == Product.ingredient_id)
        .where(
            Product.ingredient_id.in_(ids),
            Product.active,
            Product.pack_unit.is_not(None),
            Ingredient.canonical_unit == "g",
        )
    )
    packs: dict[uuid.UUID, list[str]] = defaultdict(list)
    for ingredient_id, unit in rows.tuples():
        packs[ingredient_id].append(unit)
    return {i for i, units in packs.items() if all(is_volume(u) for u in units)}


async def guard_unit_change(
    db: AsyncSession, ingredient_id: uuid.UUID, payload: IngredientUpdate
) -> None:
    """Refuse a unit change while measures in the old unit exist, unless the
    request drops them; then delete them in the same transaction as the change."""
    if payload.canonical_unit is None:
        return
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None or ingredient.canonical_unit == payload.canonical_unit:
        return  # a missing ingredient is the update's own 404
    measures = (
        (
            await db.execute(
                select(IngredientMeasure)
                .where(IngredientMeasure.ingredient_id == ingredient_id)
                .order_by(IngredientMeasure.label)
            )
        )
        .scalars()
        .all()
    )
    if payload.keep_measures is not None:
        measures = [m for m in measures if m.id not in payload.keep_measures]
    if not measures:
        return
    if not payload.drop_measures:
        unit = ingredient.canonical_unit
        raise ApiError(
            409,
            "measures_in_old_unit",
            f"{ingredient.name}'s measures are in {unit} and can't be converted without a "
            f"density. Remove them to change the unit.",
            details={
                "unit": unit,
                "measures": [
                    {"label": m.label, "canonical_qty": format(m.canonical_qty, "f")}
                    for m in measures
                ],
            },
        )
    await db.execute(
        delete(IngredientMeasure).where(IngredientMeasure.id.in_([m.id for m in measures]))
    )


# --- unit drift (#527) ------------------------------------------------------------------


async def standard_switches(
    db: AsyncSession, ingredient_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str, bool] | None]:
    """For Needs a bridge: of these ingredients, the ones with a standard entry, each
    with (the entry's unit, the switch's suggestion key, whether it was skipped) when
    its unit differs, else None. They get no "Measure in ml instead" of their own:
    one offer per ingredient, the standard list's."""
    from app.models.catalog import CleanupSkip

    ids = set(ingredient_ids)
    if not ids:
        return {}
    rows = (await db.execute(select(Ingredient).where(Ingredient.id.in_(ids)))).scalars()
    entries = standard.by_key()
    out: dict[uuid.UUID, tuple[str, str, bool] | None] = {}
    for i in rows:
        if i.slug not in entries:
            continue
        entry = _entry_of(i)
        out[i.id] = None if entry is None else (entry.unit, _key(i, entry), False)
    keys = [v[1] for v in out.values() if v is not None]
    if keys:
        skipped = set(
            (await db.execute(select(CleanupSkip.key).where(CleanupSkip.key.in_(keys)))).scalars()
        )
        out = {i: (v[0], v[1], v[1] in skipped) if v else None for i, v in out.items()}
    return out


@dataclass
class _Line:
    key: str  # what the fingerprint holds for it
    recipe_id: uuid.UUID
    text: str


@dataclass
class Plan:
    """What switching the ingredient to its entry's unit would do."""

    ingredient: Ingredient
    entry: standard.StandardEntry
    kept: list[tuple[IngredientMeasure, Decimal]] = field(default_factory=list)
    dropped: list[IngredientMeasure] = field(default_factory=list)
    added: list[standard.StandardMeasure] = field(default_factory=list)
    failing: int = 0  # prices that fail to normalize now
    unpriced: list[_Line] = field(default_factory=list)
    unconvertible: list[_Line] = field(default_factory=list)

    @property
    def sure(self) -> bool:
        return not (self.dropped or self.unpriced or self.unconvertible)


def _entry_of(ingredient: Ingredient) -> standard.StandardEntry | None:
    entry = standard.by_key().get(ingredient.slug)
    return entry if entry is not None and entry.unit != ingredient.canonical_unit else None


async def unit_mismatches(
    db: AsyncSession,
    ingredients: list[Ingredient],
    *,
    guess: bool = True,
) -> list[Suggestion]:
    """The ingredients whose unit differs from their standard entry's. With
    ``guess=False`` (the count) nothing is worked out: no query, no conversion."""
    out = []
    for ingredient in ingredients:
        entry = _entry_of(ingredient)
        if entry is None:
            continue
        if not guess:
            out.append(_bare(ingredient, entry))
            continue
        out.append(_suggestion(await plan(db, ingredient.id)))
    return out


async def for_ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> list[Suggestion]:
    """The one ingredient's switch, if it has one: the per-key builder of an accept."""
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None or not ingredient.active or _entry_of(ingredient) is None:
        return []
    return [_suggestion(await plan(db, ingredient_id))]


def _key(ingredient: Ingredient, entry: standard.StandardEntry) -> str:
    return suggestion_key("unit_mismatch", ingredient.id, entry.unit)


def _bare(ingredient: Ingredient, entry: standard.StandardEntry) -> Suggestion:
    return Suggestion(
        _key(ingredient, entry),
        "unit_mismatch",
        ingredient.id,
        ingredient.name,
        ingredient.canonical_unit,
        entry.unit,
        f"the standard list measures {entry.name} in {entry.unit}",
        entry.unit,
        confidence="guess",
    )


def _qty(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")


def _suggestion(p: Plan) -> Suggestion:
    i, e = p.ingredient, p.entry
    old, new = i.canonical_unit, e.unit
    reason = [f"the standard list measures {e.name} in {new}"]
    if p.failing:
        fails = "price fails" if p.failing == 1 else "prices fail"
        reason.append(f"{p.failing} {fails} to normalize in {old}")
    if p.dropped:
        reason.append(
            f"{len(p.dropped)} {'measure drops' if len(p.dropped) == 1 else 'measures drop'}"
        )
    if p.unconvertible:
        n = len(p.unconvertible)
        reason.append(f"{n} recipe {'line' if n == 1 else 'lines'} can no longer convert")
    if p.unpriced:
        n = len(p.unpriced)
        reason.append(f"{n} recipe {'line loses its' if n == 1 else 'lines lose their'} price")
    listed = {(m.label.lower(), m.qty) for m in e.measures}
    consequences = [
        Consequence(
            f"1 {m.label} = {_qty(m.canonical_qty)} {old} is dropped"
            + (
                " (entered by hand)"
                if m.source in BY_HAND and (m.label.lower(), m.canonical_qty) not in listed
                else ""
            )
        )
        for m in p.dropped
    ]
    consequences += [
        Consequence(f"1 {m.label} = {_qty(m.canonical_qty)} {old} becomes {_qty(qty)} {new}")
        for m, qty in p.kept
        if qty != m.canonical_qty
    ]
    consequences += [
        Consequence(f"1 {m.label} = {_qty(m.qty)} {new} is added from the standard list")
        for m in p.added
    ]
    consequences += [
        Consequence(f"{line.text} can no longer convert", line.recipe_id)
        for line in p.unconvertible
    ]
    consequences += [
        Consequence(f"{line.text} loses its price", line.recipe_id) for line in p.unpriced
    ]
    measures = sorted((str(m.id), m.canonical_qty, m.source) for m in i.measures)
    lines = sorted(line.key for line in (*p.unpriced, *p.unconvertible))
    return Suggestion(
        _key(i, e),
        "unit_mismatch",
        i.id,
        i.name,
        old,
        new,
        "; ".join(reason),
        new,
        confidence="sure" if p.sure else "guess",
        consequences=tuple(consequences),
        fingerprint=fingerprint(new, old, *measures, *lines),
        measures_dropped=len(p.dropped),
    )


async def plan(db: AsyncSession, ingredient_id: uuid.UUID) -> Plan:
    """What the switch would do now, read from the ingredient, its prices and the
    latest cost snapshots."""
    ingredient = (
        await db.execute(
            select(Ingredient)
            .options(selectinload(Ingredient.measures))
            .where(Ingredient.id == ingredient_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    entry = standard.by_key()[ingredient.slug]
    out = Plan(ingredient, entry)
    now = await build_context(db, ingredient, None)
    bare = replace(now, canonical_unit=entry.unit, measures=())
    for m in sorted(ingredient.measures, key=lambda m: m.label.lower()):
        moved = convert(m.canonical_qty, ingredient.canonical_unit, bare)
        if isinstance(moved, ConversionFailure):
            out.dropped.append(m)
        else:
            out.kept.append((m, moved.qty.quantize(_PLACES)))
    labels = {m.label.lower() for m, _ in out.kept}
    out.added = [m for m in entry.measures if m.label.lower() not in labels]
    after = replace(
        bare,
        measures=tuple(
            [Measure(m.label, qty, m.source, m.confirmed) for m, qty in out.kept]
            + [Measure(m.label, m.qty, "manual", False) for m in out.added]
        ),
    )
    failing_after = await _prices(db, ingredient, after, out)
    await _lines(db, ingredient, now, after, failing_after, out)
    return out


async def _prices(
    db: AsyncSession, ingredient: Ingredient, after: ConversionContext, out: Plan
) -> set[uuid.UUID]:
    """Count the prices failing now; return those that normalize now and wouldn't after."""
    rows = await db.execute(
        select(
            PriceObservation.id,
            PriceObservation.qty,
            PriceObservation.unit,
            PriceNorm.status,
            Product,
        )
        .join(Product, Product.id == PriceObservation.product_id)
        .join(PriceNorm, PriceNorm.observation_id == PriceObservation.id)
        .where(
            Product.ingredient_id == ingredient.id,
            ~select(PriceObservationVoid.id)
            .where(PriceObservationVoid.observation_id == PriceObservation.id)
            .exists(),
        )
    )
    failing: set[uuid.UUID] = set()
    for obs_id, qty, unit, status, product in rows:
        if status != "ok":
            out.failing += 1
            continue
        context = await build_context(db, ingredient, product)
        moved = convert(qty, unit, replace(after, product=context.product))
        if isinstance(moved, ConversionFailure):
            failing.add(obs_id)
    return failing


async def _lines(
    db: AsyncSession,
    ingredient: Ingredient,
    now: ConversionContext,
    after: ConversionContext,
    failing: set[uuid.UUID],
    out: Plan,
) -> None:
    """The recipe lines of the ingredient that would lose their price or stop converting."""
    if failing:
        rows = await db.execute(
            select(RecipeCostLine.id, RecipeCostLine.status, RecipeCostLine.observation_id,
                   RecipeIngredient.raw_name, Recipe.id, Recipe.title)
            .join(RecipeCostSnapshot, RecipeCostSnapshot.id == RecipeCostLine.snapshot_id)
            .join(Recipe, (Recipe.id == RecipeCostSnapshot.recipe_id)
                  & (Recipe.content_hash == RecipeCostSnapshot.content_hash))
            .join(RecipeIngredient, RecipeIngredient.id == RecipeCostLine.recipe_ingredient_id)
            .where(
                RecipeCostLine.observation_id.in_(failing),
                RecipeCostLine.status.in_(("priced", "posted")),
                RecipeCostSnapshot.basis == "latest",
                RecipeCostSnapshot.window_days.is_(None),
                RecipeCostSnapshot.min_quality.is_(None),
            )
            .order_by(Recipe.title, RecipeIngredient.seq)
        )  # fmt: skip
        for line_id, status, obs, raw, recipe_id, title in rows:
            out.unpriced.append(_Line(f"{line_id}:{status}:{obs}", recipe_id, f"{title}: {raw}"))
    rows = await db.execute(
        select(RecipeIngredient, Recipe.title)
        .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
        .where(
            RecipeIngredient.ingredient_id == ingredient.id,
            RecipeIngredient.qty.is_not(None),
            RecipeIngredient.negligible.is_(False),
        )
        .order_by(Recipe.title, RecipeIngredient.seq)
    )
    for line, title in rows:
        unit = from_unit_of(line)
        if isinstance(convert(line.qty, unit, now), ConversionFailure):
            continue
        if isinstance(convert(line.qty, unit, after), ConversionFailure):
            out.unconvertible.append(
                _Line(f"{line.id}:unconvertible", line.recipe_id, f"{title}: {line.raw_name}")
            )


async def lock(db: AsyncSession, ingredient_id: uuid.UUID) -> None:
    """Hold the ingredient's row until the switch commits: a second one waits, then
    finds it gone."""
    await db.execute(select(Ingredient.id).where(Ingredient.id == ingredient_id).with_for_update())


async def accept(db: AsyncSession, s: Suggestion) -> dict[str, int]:
    """Switch the ingredient to its entry's unit in the caller's transaction (no
    commit): drop the measures that can't convert, rewrite the rest, add the
    entry's missing ones, then change the unit, which renormalizes its prices and
    recomputes its recipes."""
    p = await plan(db, s.subject_id)
    payload = IngredientUpdate(
        canonical_unit=p.entry.unit,
        drop_measures=True,
        keep_measures={m.id for m, _ in p.kept},
    )
    await guard_unit_change(db, p.ingredient.id, payload)
    rewritten = sum(qty != m.canonical_qty for m, qty in p.kept)
    for m, qty in p.kept:
        m.canonical_qty = qty
    await db.flush()
    await catalog.apply_standard_entry(db, p.ingredient, p.entry)
    prices = await catalog.update_ingredient_core(db, p.ingredient.id, payload)
    recipes = await db.scalar(
        select(func.count(distinct(RecipeIngredient.recipe_id))).where(
            RecipeIngredient.ingredient_id == p.ingredient.id
        )
    )
    return {
        "prices_renormalized": prices,
        "recipes_recomputed": recipes or 0,
        "measures_dropped": len(p.dropped),
        "measures_rewritten": rewritten,
    }
