"""Costing a line from another recipe (07, 3D; #526, rulings S1–S4).

A line costs from a recipe in two cases: a recipe reference
(``@./basics/simple-syrup{1%fl oz}``, resolution ``recipe``), and route (b), a
line whose ingredient names the recipe that makes it (``made_by_recipe_id``)
when no real or posted product price qualifies.

**Cycles first.** ``recipe_graph`` reads every edge in one query (referrer →
``ref_recipe_id``, and referrer → its line ingredients' ``made_by_recipe_id``)
and splits it into strongly connected components (``app.recipes.refs``). A
line whose source recipe sits in the referrer's own component is ``cycle``, and
no snapshot of it is read: a stored snapshot from an earlier pass would
otherwise let two recipes in a cycle feed each other's cost. Recursion follows
only edges out of a component, and the components form a DAG, so it ends; this
is the only guard, with no depth limit.

**The source's cost.** Under the referrer's basis, window and quality floor:
the source's current snapshot of the current ``COSTING_VERSION``, or one
computed on demand in the referrer's transaction (under a savepoint, re-read
when a concurrent session wrote the same key first). A per-kitchen view, and
the ``offers`` basis, cost the source unsaved under ``latest`` with the same
kitchen; the line then reads ``source_basis = "latest"``.

**Scaling.** The line's quantity in the source's makes unit over what it
makes: ``factor = qty / makes_qty``, never quantized, so a repeating fraction
does not compound down a chain; only the line's cost is quantized, once. A
reference line converts within one dimension only. Route (b) has the
ingredient's density and measures too, so ``1 fl oz`` of a syrup can scale a
yield in g when the syrup has a density.

**Outcomes.** ``source_recipe_id`` is set on every one, failures included:
- no yield: ``unconvertible`` ``no_yield``; one that doesn't parse: ``bad_yield``;
- a unit that can't reach the yield's: ``yield_unit`` on a reference line, and
  the conversion's own code (``no_density``) on route (b);
- no recipe at the path, or a missing source: ``unmapped`` ``no_recipe``;
- a source that doesn't parse: ``unpriced`` ``recipe_unreadable``;
- a cycle: ``unpriced`` ``cycle``;
- a source with no counted or posted cost: ``unpriced`` ``recipe_unpriced``;
- a source with any posted part: ``posted``, factor × (counted + posted), so
  posted money never enters the counted totals (#523, P2);
- else ``priced``, factor × the source's consumed cost. The basket adds the
  same amount: a batch made for many drinks is not charged whole per drink.
A source that is itself incomplete marks the line ``source_partial``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ingredient, Recipe, RecipeCostSnapshot, RecipeIngredient
from app.recipes import refs
from app.services.packs import CTX as _CTX
from app.services.packs import money as _money
from app.services.recipe_cost_lines import LineCost, _usable, from_unit_of
from app.services.units import build_context, load_units
from app.units import ConversionFailure, convert, convert_between

# --- the link graph ----------------------------------------------------------------------

_EDGES = """
    SELECT ri.recipe_id AS referrer, ri.ref_recipe_id AS source
    FROM recipe_ingredient ri
    WHERE ri.ref_recipe_id IS NOT NULL
    UNION
    SELECT ri.recipe_id, coalesce(s.made_by_recipe_id, i.made_by_recipe_id)
    FROM recipe_ingredient ri
    JOIN ingredient i ON i.id = ri.ingredient_id
    LEFT JOIN ingredient s ON s.id = i.merged_into
    WHERE coalesce(s.made_by_recipe_id, i.made_by_recipe_id) IS NOT NULL
"""
_REFERRERS = """
    SELECT ri.recipe_id
    FROM recipe_ingredient ri
    WHERE ri.ref_recipe_id = ANY(CAST(:ids AS uuid[]))
    UNION
    SELECT ri.recipe_id
    FROM recipe_ingredient ri
    JOIN ingredient i ON i.id = ri.ingredient_id
    LEFT JOIN ingredient s ON s.id = i.merged_into
    WHERE coalesce(s.made_by_recipe_id, i.made_by_recipe_id) = ANY(CAST(:ids AS uuid[]))
"""


@dataclass(slots=True)
class RecipeGraph:
    """Which recipe costs from which, and the cycles among them.

    An edge exists for every line whose ingredient has a made-by recipe, even
    when a product price covers that line: only lines actually sent to a recipe
    check the component, so an unused edge never makes a false ``cycle``; it
    only groups the recompute order.
    """

    edges: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict)
    order: list[list[uuid.UUID]] = field(default_factory=list)  # components, children first
    component: dict[uuid.UUID, int] = field(default_factory=dict)

    @classmethod
    def of(cls, edges: dict[uuid.UUID, set[uuid.UUID]]) -> RecipeGraph:
        order = refs.components(edges, edges)
        component = {member: n for n, members in enumerate(order) for member in members}
        return cls(edges=edges, order=order, component=component)

    @property
    def empty(self) -> bool:
        return not self.edges

    def same_component(self, a: uuid.UUID, b: uuid.UUID) -> bool:
        if a == b:
            return True
        return a in self.component and self.component.get(a) == self.component.get(b)

    def cycle_of(self, recipe_id: uuid.UUID) -> list[uuid.UUID]:
        """The members of the recipe's cycle in ``str(id)`` order, or [] when it is in none."""
        n = self.component.get(recipe_id)
        if n is None:
            return []
        members = self.order[n]
        if len(members) == 1 and recipe_id not in self.edges.get(recipe_id, ()):
            return []
        return sorted(members, key=str)

    def chain(self, recipe_id: uuid.UUID) -> list[uuid.UUID]:
        """One walk round the recipe's cycle back to it ("A → B → A"), or [] when none."""
        cycle = self.cycle_of(recipe_id)
        return refs.chain(cycle, self.edges, recipe_id) if cycle else []

    def cycles(self) -> list[list[uuid.UUID]]:
        return [members for members in self.order if self.cycle_of(members[0])]

    def children_first(self, recipe_ids: set[uuid.UUID]) -> list[uuid.UUID]:
        """The recipes in an order where a source comes before what costs from it;
        a cycle's members together, by ``str(id)``; recipes outside the graph last,
        by ``str(id)``."""
        placed: list[uuid.UUID] = []
        for members in self.order:
            placed.extend(sorted((m for m in members if m in recipe_ids), key=str))
        rest = sorted(recipe_ids - set(placed), key=str)
        return placed + rest


async def recipe_graph(db: AsyncSession) -> RecipeGraph:
    """Every recipe-to-recipe edge, in one query."""
    edges: dict[uuid.UUID, set[uuid.UUID]] = {}
    for referrer, source in (await db.execute(text(_EDGES))).tuples():
        edges.setdefault(referrer, set()).add(source)
    return RecipeGraph.of(edges)


async def referrers_of(db: AsyncSession, recipe_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """The recipes with a line that costs, or may cost, from one of these."""
    if not recipe_ids:
        return set()
    rows = await db.execute(
        text(_REFERRERS),
        {"ids": [str(i) for i in recipe_ids]},
    )
    return set(rows.scalars())


# --- the source's cost --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceCost:
    """What a source recipe came to under the referrer's key."""

    consumed: Decimal | None
    consumed_high: Decimal | None
    posted: Decimal | None
    posted_high: Decimal | None
    partial: bool  # some line of it has no price at all, or is itself partial
    lines_priced: int
    lines_total: int
    basis: str  # the basis it was costed under ("latest" for a view or offers)


def _partial(priced: int, posted: int, negligible: int, partial: int, total: int) -> bool:
    return partial > 0 or priced + posted + negligible < total


def _from_snapshot(snapshot: RecipeCostSnapshot) -> SourceCost:
    return SourceCost(
        consumed=snapshot.consumed_cost,
        consumed_high=snapshot.consumed_cost_high,
        posted=snapshot.posted_consumed_cost,
        posted_high=snapshot.posted_consumed_cost_high,
        partial=_partial(
            snapshot.lines_priced,
            snapshot.lines_posted,
            snapshot.lines_negligible,
            snapshot.lines_partial,
            snapshot.lines_total,
        ),
        lines_priced=snapshot.lines_priced,
        lines_total=snapshot.lines_total,
        basis=snapshot.basis,
    )


async def source_cost(
    db: AsyncSession,
    source: Recipe,
    basis: str,
    window: int | None,
    min_quality: int | None,
    home_base_id: uuid.UUID | None,
    graph: RecipeGraph,
) -> SourceCost:
    """The source recipe's cost under the referrer's key (see the module docstring)."""
    from app.services import recipe_costing  # it imports this module

    if home_base_id is not None or basis == "offers":
        costing = await recipe_costing.cost_recipe(
            db, source, "latest", None, min_quality, home_base_id=home_base_id, graph=graph
        )
        return SourceCost(
            consumed=costing.consumed_cost,
            consumed_high=costing.consumed_cost_high,
            posted=costing.posted_consumed_cost,
            posted_high=costing.posted_consumed_cost_high,
            partial=_partial(
                len(costing.priced),
                len(costing.posted),
                costing.count("negligible"),
                costing.partial_count,
                len(costing.costs),
            ),
            lines_priced=len(costing.priced),
            lines_total=len(costing.costs),
            basis="latest",
        )
    key = (basis, window, min_quality)
    snapshot = await recipe_costing.find_snapshot(db, source, *key, current_version=True)
    if snapshot is None:
        try:
            async with db.begin_nested():
                snapshot = await recipe_costing.compute_snapshot(
                    db, source, *key, commit=False, graph=graph
                )
        except IntegrityError:
            # A concurrent session (a page read racing a trigger) wrote this key first.
            snapshot = await recipe_costing.find_snapshot(db, source, *key)
            assert snapshot is not None  # noqa: S101
    return _from_snapshot(snapshot)


# --- one line ---------------------------------------------------------------------------


def may_take_route_b(cost: LineCost, ingredient: Ingredient | None) -> bool:
    """A line on an ingredient a recipe makes that nothing priced (#526, route b)."""
    return (
        cost.status in ("unpriced", "unconvertible")
        and cost.source_recipe_id is None
        and ingredient is not None
        and ingredient.made_by_recipe_id is not None
    )


def takes_route_b(
    cost: LineCost,
    ingredient: Ingredient,
    pin: uuid.UUID | None,
    prices: dict,
    posted: dict,
) -> bool:
    """After the posted pass: still unpriced, or unconvertible only at the
    canonical conversion with no product price in sight (a real or posted price
    always wins, ruling S2; there the fix is the conversion)."""
    if cost.status == "unpriced":
        return True
    if cost.status != "unconvertible":
        return False  # a posted price took it
    unit = ingredient.canonical_unit
    return not (
        _usable(prices.get(ingredient.id, []), unit, pin)
        or _usable(posted.get(ingredient.id, []), unit, pin)
    )


@dataclass(slots=True)
class SourceRouter:
    """One recipe's costing context for its lines that cost from a recipe; the
    link graph is read on the first such line, never when there is none."""

    db: AsyncSession
    referrer: Recipe
    basis: str
    window: int | None
    min_quality: int | None
    home_base_id: uuid.UUID | None
    graph: RecipeGraph | None = None

    async def route(
        self,
        out: LineCost,
        line: RecipeIngredient,
        source_id: uuid.UUID | None,
        ingredient: Ingredient | None,
    ) -> None:
        if self.graph is None:
            self.graph = await recipe_graph(self.db)
        await cost_from_recipe(
            self.db,
            out,
            line,
            self.referrer,
            source_id,
            ingredient,
            self.basis,
            self.window,
            self.min_quality,
            self.home_base_id,
            self.graph,
        )


@dataclass(slots=True)
class Scaled:
    factor: Decimal
    factor_high: Decimal | None
    qty: Decimal  # the line's quantity in the makes unit
    qty_high: Decimal | None


async def _scale(
    db: AsyncSession,
    line: RecipeIngredient,
    source: Recipe,
    ingredient: Ingredient | None,
) -> Scaled | str:
    """The line's share of one batch of ``source``, or the failure code."""
    assert source.makes_qty is not None and source.makes_unit is not None  # noqa: S101
    assert line.qty is not None  # noqa: S101
    units = await load_units(db)
    makes_unit = units.get(source.makes_unit)
    line_unit = units.get(line.unit) if line.unit else None
    quantities = [line.qty] + ([line.qty_high] if line.qty_high is not None else [])
    if (
        line_unit is not None
        and makes_unit is not None
        and line_unit.dimension == makes_unit.dimension
    ):
        within = [convert_between(q, line_unit.code, makes_unit.code, units) for q in quantities]
        return _scaled(within, source.makes_qty)
    if ingredient is None:
        return "yield_unit"  # a reference line has no density or measures to cross with
    context = await build_context(db, ingredient, None)
    makes = convert(source.makes_qty, source.makes_unit, context)
    if isinstance(makes, ConversionFailure):
        return makes.code
    found: list[Decimal] = []
    for q in quantities:
        converted = convert(q, from_unit_of(line), context)
        if isinstance(converted, ConversionFailure):
            return converted.code
        found.append(converted.qty)
    # Both are in the ingredient's canonical unit; the line's share is their ratio.
    in_makes = [_CTX.divide(_CTX.multiply(q, source.makes_qty), makes.qty) for q in found]
    return _scaled(in_makes, source.makes_qty)


def _scaled(in_makes: list[Decimal], makes_qty: Decimal) -> Scaled:
    low = in_makes[0]
    high = in_makes[1] if len(in_makes) > 1 else None
    return Scaled(
        factor=_CTX.divide(low, makes_qty),
        factor_high=_CTX.divide(high, makes_qty) if high is not None else None,
        qty=low,
        qty_high=high,
    )


async def cost_from_recipe(
    db: AsyncSession,
    out: LineCost,
    line: RecipeIngredient,
    referrer: Recipe,
    source_id: uuid.UUID | None,
    ingredient: Ingredient | None,
    basis: str,
    window: int | None,
    min_quality: int | None,
    home_base_id: uuid.UUID | None,
    graph: RecipeGraph,
) -> None:
    """Fill ``out`` for a line that costs from the recipe ``source_id``."""
    out.source_recipe_id = source_id
    out.canonical_qty = out.canonical_qty_high = out.canonical_unit = None
    out.product_id = out.observation_id = out.norm_unit_price = None
    out.consumed_cost = out.consumed_cost_high = out.basket_cost = out.basket_cost_high = None
    out.packs = out.packs_high = out.yield_applied = None
    out.bridge_kind = out.bridge_confirmed = out.offer = None
    out.failure_code = None
    source = await db.get(Recipe, source_id) if source_id is not None else None
    if source is None or source.status == "missing":
        out.status, out.failure_code = "unmapped", "no_recipe"
        return
    if source.status == "parse_error":
        out.status, out.failure_code = "unpriced", "recipe_unreadable"
        return
    if graph.same_component(referrer.id, source.id):
        out.status, out.failure_code = "unpriced", "cycle"
        return
    if source.makes_qty is None or source.makes_unit is None:
        out.status = "unconvertible"
        out.failure_code = "bad_yield" if source.makes_text else "no_yield"
        return
    scaled = await _scale(db, line, source, ingredient)
    if isinstance(scaled, str):
        out.status, out.failure_code = "unconvertible", scaled
        return
    out.canonical_qty, out.canonical_qty_high = scaled.qty, scaled.qty_high
    out.canonical_unit = source.makes_unit
    child = await source_cost(db, source, basis, window, min_quality, home_base_id, graph)
    out.source_partial = child.partial
    out.source_basis = child.basis if child.basis != basis else None
    if child.consumed is None and child.posted is None:
        out.status, out.failure_code = "unpriced", "recipe_unpriced"
        out.source_partial = False
        return
    high_factor = scaled.factor_high
    if child.posted is not None:
        out.status = "posted"
        low = (child.consumed or Decimal(0)) + child.posted
        high = (child.consumed_high or child.consumed or Decimal(0)) + (
            child.posted_high or child.posted
        )
    else:
        out.status = "priced"
        low = child.consumed or Decimal(0)
        high = child.consumed_high or low
    out.consumed_cost = _money(_CTX.multiply(scaled.factor, low))
    needs_high = high_factor is not None or high != low
    if needs_high:
        out.consumed_cost_high = _money(_CTX.multiply(high_factor or scaled.factor, high))
    out.basket_cost, out.basket_cost_high = out.consumed_cost, out.consumed_cost_high
