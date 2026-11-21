"""Read models for recipes that cost from recipes (07, 3D; #526, Part 4).

What a recipe makes, in the household's display units ("Makes 16.9 fl oz (500
ml)"), and its counted cost per unit of it; which recipes cost from it; what a
cost line from a recipe needs for its message; and the ingredient's made-by
link: its validation, the recipe titled like it to offer, and the warning when
a link closes a cycle.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import (
    Ingredient,
    Recipe,
    RecipeCostLine,
    RecipeCostSnapshot,
    RecipeIngredient,
)
from app.recipes.index import normalize_title
from app.recipes.refs import target_path
from app.schemas.catalog import IngredientSummary
from app.schemas.recipe_sources import (
    LinkWarning,
    MadeByRecipeOut,
    MakesOut,
    MakesUnitCost,
    RecipeLink,
)
from app.schemas.recipes import CostLineSourceOut, CycleHop, SourceCounts
from app.services import recipe_cost_sources, recipe_costing, unit_display
from app.services.recipe_cost_lines import BASES
from app.units import display
from app.units.table import SEED_UNITS, units_by_code

_UNITS = units_by_code(SEED_UNITS)
_ONE = Decimal("0.1")


def link(recipe: Recipe) -> RecipeLink:
    return RecipeLink(id=recipe.id, title=recipe.title, path=recipe.path, status=recipe.status)


# --- what a recipe makes -------------------------------------------------------------------


def _base(recipe: Recipe) -> tuple[Decimal, str] | None:
    """What one batch makes in g or ml, or None without an amount and a unit."""
    if recipe.makes_qty is None or recipe.makes_unit not in _UNITS:
        return None
    unit = _UNITS[recipe.makes_unit]
    if unit.dimension not in ("mass", "volume"):
        return None
    base = "g" if unit.dimension == "mass" else "ml"
    return recipe.makes_qty * unit.to_base_factor, base


def _display_code(base_qty: Decimal, base: str) -> str:
    """fl oz or L for a volume; for a mass, per lb, or per oz under a pound (kg metric)."""
    system = unit_display.system()
    mass = display.mass_unit(system, base_qty if base == "g" else None)
    code = display.display_unit(base, system, mass)
    assert code is not None  # noqa: S101
    return code


def makes_out(recipe: Recipe) -> MakesOut | None:
    if recipe.makes_text is None:
        return None
    found = _base(recipe)
    if found is None:
        return MakesOut(text=recipe.makes_text)
    base_qty, base = found
    code = _display_code(base_qty, base)
    shown = (base_qty / _UNITS[code].to_base_factor).quantize(_ONE, rounding=ROUND_HALF_UP)
    return MakesOut(
        text=recipe.makes_text,
        qty=recipe.makes_qty,
        unit=recipe.makes_unit,
        display_qty=shown.normalize() if shown == shown.to_integral() else shown,
        display_unit=display.LABELS[code],
    )


def _complete(snapshot: RecipeCostSnapshot) -> bool:
    return (
        snapshot.lines_priced + snapshot.lines_negligible >= snapshot.lines_total
        and snapshot.lines_partial == 0
    )


def unit_cost(
    recipe: Recipe, snapshot: RecipeCostSnapshot | None, source_basis: str | None = None
) -> MakesUnitCost | None:
    """The snapshot's counted cost per display unit of what the recipe makes."""
    found = _base(recipe)
    if found is None or snapshot is None or snapshot.consumed_cost is None:
        return None
    base_qty, base = found
    code = _display_code(base_qty, base)
    units = base_qty / _UNITS[code].to_base_factor
    return MakesUnitCost(
        amount=display.round_price(snapshot.consumed_cost / units),
        unit=display.LABELS[code],
        partial=not _complete(snapshot),
        source_basis=source_basis,
    )


async def cost_unit_cost(
    db: AsyncSession, recipe: Recipe, snapshot: RecipeCostSnapshot, home_base_id: uuid.UUID | None
) -> MakesUnitCost | None:
    """For the cost table: the snapshot's own, or under a kitchen's view or the offers
    basis, the stored ``latest`` snapshot's, marked so."""
    if home_base_id is None and snapshot.basis in BASES:
        return unit_cost(recipe, snapshot)
    stored = await recipe_costing.find_snapshot(db, recipe)
    return unit_cost(recipe, stored, "latest")


# --- which recipes cost from one ------------------------------------------------------------

_USED_IN_SQL = """
    SELECT r.id, r.title, r.path, r.status, count(*) AS lines
    FROM recipe_ingredient ri
    JOIN recipe r ON r.id = ri.recipe_id
    LEFT JOIN ingredient i ON i.id = ri.ingredient_id
    LEFT JOIN ingredient s ON s.id = i.merged_into
    WHERE ri.ref_recipe_id = :id
       OR coalesce(s.made_by_recipe_id, i.made_by_recipe_id) = :id
    GROUP BY r.id, r.title, r.path, r.status
    ORDER BY r.title, r.path
"""


async def used_in(db: AsyncSession, recipe_id: uuid.UUID) -> tuple[list[RecipeLink], int]:
    """Each recipe that costs from this one by either route, once, by title; and
    how many of their lines do."""
    rows = (await db.execute(text(_USED_IN_SQL), {"id": recipe_id})).mappings().all()
    links = [
        RecipeLink(id=r["id"], title=r["title"], path=r["path"], status=r["status"]) for r in rows
    ]
    return links, sum(r["lines"] for r in rows)


# --- a cost line's source --------------------------------------------------------------------


async def _suggestion(db: AsyncSession, written: str) -> RecipeLink | None:
    """A recipe whose file has the reference's base name, somewhere else."""
    name = written.rstrip("/").rsplit("/", 1)[-1].removesuffix(".cook")
    if not name:
        return None
    found = (
        (
            await db.execute(
                select(Recipe)
                .where(
                    Recipe.status != "missing",
                    (Recipe.path == f"{name}.cook") | Recipe.path.endswith(f"/{name}.cook"),
                )
                .order_by(Recipe.path)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return link(found) if found is not None else None


async def _hops(
    db: AsyncSession, graph: recipe_cost_sources.RecipeGraph, start: uuid.UUID
) -> list[CycleHop]:
    """The walk round ``start``'s cycle, each recipe with how it reaches the next."""
    walk = graph.chain(start)
    if not walk:
        return []
    recipes = {
        r.id: r for r in (await db.execute(select(Recipe).where(Recipe.id.in_(walk)))).scalars()
    }
    hops: list[CycleHop] = []
    for here, there in zip(walk, [*walk[1:], None], strict=True):
        via = None
        if there is not None:
            by_file = await db.scalar(
                select(RecipeIngredient.id).where(
                    RecipeIngredient.recipe_id == here, RecipeIngredient.ref_recipe_id == there
                )
            )
            if by_file is None:
                via = await db.scalar(
                    select(Ingredient)
                    .join(RecipeIngredient, RecipeIngredient.ingredient_id == Ingredient.id)
                    .where(
                        RecipeIngredient.recipe_id == here, Ingredient.made_by_recipe_id == there
                    )
                    .limit(1)
                )
        hops.append(
            CycleHop(
                recipe=link(recipes[here]),
                via_ingredient=IngredientSummary.model_validate(via) if via else None,
            )
        )
    return hops


async def line_sources(
    db: AsyncSession,
    snapshot: RecipeCostSnapshot,
    rows: list[tuple[RecipeCostLine, RecipeIngredient]],
    home_base_id: uuid.UUID | None,
) -> dict[uuid.UUID, dict]:
    """By cost line id, the ``source_*`` fields of each line from a recipe."""
    wanted = [(line, ri) for line, ri in rows if line.source_recipe_id or ri.resolution == "recipe"]
    if not wanted:
        return {}
    referrer = await db.get(Recipe, snapshot.recipe_id)
    assert referrer is not None  # noqa: S101
    graph = None
    if any(line.failure_code == "cycle" for line, _ in wanted):
        graph = await recipe_cost_sources.recipe_graph(db)
    on_latest = (home_base_id is not None or snapshot.basis == "offers") and (
        snapshot.basis != "latest"
    )
    out: dict[uuid.UUID, dict] = {}
    for line, ri in wanted:
        source = await db.get(Recipe, line.source_recipe_id) if line.source_recipe_id else None
        detail = CostLineSourceOut(
            recipe=link(source) if source else None,
            makes=makes_out(source) if source else None,
        )
        if ri.ref_path:
            detail.target_path = target_path(referrer.path, ri.ref_path)
            if line.failure_code == "no_recipe":
                detail.suggestion = await _suggestion(db, ri.ref_path)
        if line.failure_code == "recipe_unpriced" and source is not None:
            key = snapshot.basis if snapshot.basis in BASES and home_base_id is None else "latest"
            theirs = await recipe_costing.find_snapshot(
                db,
                source,
                key,
                snapshot.window_days if key == snapshot.basis else None,
                snapshot.min_quality,
            )
            if theirs is not None:
                detail.counts = SourceCounts(priced=theirs.lines_priced, total=theirs.lines_total)
        if line.failure_code == "cycle" and graph is not None:
            detail.cycle = await _hops(db, graph, referrer.id)
        out[line.id] = {
            "source_recipe": detail.recipe,
            "source_partial": line.source_partial,
            "source_basis": "latest" if on_latest and source is not None else None,
            "source_counts": detail.counts,
            "source": detail,
        }
    return out


# --- the ingredient's made-by link ------------------------------------------------------------


async def checked_recipe(db: AsyncSession, recipe_id: uuid.UUID | None) -> uuid.UUID | None:
    """The recipe an ingredient may be linked to; 422 when it can't cost anything."""
    if recipe_id is None:
        return None
    recipe = await db.get(Recipe, recipe_id)
    if recipe is None:
        raise ApiError(422, "recipe_not_found", "No such recipe.")
    if recipe.status == "missing":
        raise ApiError(
            422,
            "recipe_missing",
            f"The file {recipe.path} is gone. Restore it, or link another recipe.",
            details={"path": recipe.path},
        )
    if recipe.status == "parse_error":
        raise ApiError(
            422,
            "recipe_unreadable",
            f"{recipe.path} can't be read. Fix it, then rescan.",
            details={"path": recipe.path},
        )
    return recipe.id


async def made_by_out(db: AsyncSession, ingredient: Ingredient) -> MadeByRecipeOut | None:
    if ingredient.made_by_recipe_id is None:
        return None
    recipe = await db.get(Recipe, ingredient.made_by_recipe_id)
    if recipe is None:
        return None
    stored = await recipe_costing.find_snapshot(db, recipe)
    return MadeByRecipeOut(
        id=recipe.id,
        title=recipe.title,
        path=recipe.path,
        status=recipe.status,
        makes_text=recipe.makes_text,
        makes_ok=_base(recipe) is not None,
        unit_cost=unit_cost(recipe, stored),
    )


_HAS_PRICE_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM price_current pc JOIN product p ON p.id = pc.product_id
        WHERE p.ingredient_id = :id
    )
"""


async def recipe_match(db: AsyncSession, ingredient: Ingredient) -> RecipeLink | None:
    """The one readable recipe titled like the ingredient, to offer as its maker,
    when nothing is linked and no product of it has a price."""
    if ingredient.made_by_recipe_id is not None:
        return None
    if (await db.execute(text(_HAS_PRICE_SQL), {"id": ingredient.id})).scalar():
        return None
    name = normalize_title(ingredient.name)
    if not name:
        return None
    found = [
        r
        for r in (await db.execute(select(Recipe).where(Recipe.status == "ok"))).scalars()
        if normalize_title(r.title) == name
    ]
    return link(found[0]) if len(found) == 1 else None


async def cycle_warning(db: AsyncSession, recipe_id: uuid.UUID) -> LinkWarning | None:
    """Whether the recipe now sits on a cycle, with the chain of titles."""
    graph = await recipe_cost_sources.recipe_graph(db)
    walk = graph.chain(recipe_id)
    if not walk:
        return None
    recipes = {
        r.id: r for r in (await db.execute(select(Recipe).where(Recipe.id.in_(walk)))).scalars()
    }
    titles = " → ".join(recipes[r].title for r in walk)
    return LinkWarning(
        code="recipe_cycle",
        message=f"Linked, but these recipes now cost from each other: {titles}. "
        "Their lines read as a cycle until one of them stops using the other.",
        chain=[link(recipes[r]) for r in walk],
    )
