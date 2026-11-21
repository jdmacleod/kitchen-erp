"""Recipes (07, Phase 3): the indexed repository as the API shows it (3A, 3B),
and the resolve queue, decisions and pins (3C)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import IngredientCreate, IngredientSummary
from app.schemas.recipe_sources import MakesOut, MakesUnitCost, RecipeLink

RecipeStatus = Literal["ok", "parse_error", "missing"]
# Whether every line of the current cost is priced; a recipe with no snapshot is incomplete.
Completeness = Literal["complete", "incomplete"]
MountState = Literal["mounted", "missing", "empty", "no_cook_files"]
QtyKind = Literal["number", "range", "text", "none"]
Resolution = Literal["alias", "manual", "unmatched", "negligible", "ignored", "recipe"]


class RecipeSummary(ApiModel):
    id: uuid.UUID
    path: str
    title: str
    servings: DecimalStr | None
    servings_text: str | None
    status: RecipeStatus
    dirty: bool
    content_hash: str
    last_indexed_at: datetime


class RelinkProposal(ApiModel):
    """The new file the indexer believes a missing recipe became (3A, step 3)."""

    target_id: uuid.UUID
    path: str
    title: str
    reason: str


class RecipeIngredientOut(ApiModel):
    """One ingredient reference as written, in document order (3B); resolution is 3C's."""

    id: uuid.UUID
    seq: int
    section: str | None
    raw_name: str
    name_norm: str
    qty_kind: QtyKind
    qty: DecimalStr | None
    qty_high: DecimalStr | None
    qty_text: str | None
    unit_text: str | None
    unit: str | None
    note: str | None
    negligible: bool
    resolution: Resolution
    ingredient_id: uuid.UUID | None
    ingredient_name: str | None = None
    # A recipe reference (#526): the path as written and the recipe it names.
    ref_path: str | None = None
    ref_recipe_id: uuid.UUID | None = None


class RecipePinOut(ApiModel):
    """A line pinned to one product (3C), by the name as normalized."""

    name_norm: str
    product_id: uuid.UUID
    product_name: str
    brand: str | None


# --- the rendered body (package 8) -------------------------------------------------------
#
# The parsed file as the recipe page renders it: sections of steps of items, in
# the order written. Quantities are text ("2", "1–2", "a handful"), never floats.


class BodyText(ApiModel):
    t: Literal["text"]
    v: str


class BodyNote(ApiModel):
    """A `>` note: a step's only item, shown beside the steps and never numbered."""

    t: Literal["note"]
    v: str


class BodyIngredient(ApiModel):
    """An ingredient reference; ``seq`` is its ``recipe_ingredient`` row, from 1."""

    t: Literal["ingredient"]
    name: str
    qty: str | None = None
    unit: str | None = None
    note: str | None = None
    seq: int
    # A recipe reference (#526): the path as written, and the recipe it links
    # to when one is at that path (filled in when the recipe is read).
    ref_path: str | None = None
    recipe_id: uuid.UUID | None = None


class BodyReference(ApiModel):
    """An `@&` reference to an earlier ingredient, step or section; it has no row.

    ``section`` and ``step`` index the body's sections and that section's steps
    (from 0); ``step`` is None when the whole section is meant.
    """

    t: Literal["reference"]
    name: str
    qty: str | None = None
    unit: str | None = None
    note: str | None = None
    target: Literal["ingredient", "step", "section"]
    section: int
    step: int | None = None


class BodyCookware(ApiModel):
    t: Literal["cookware"]
    name: str
    qty: str | None = None


class BodyTimer(ApiModel):
    t: Literal["timer"]
    name: str | None = None
    qty: str | None = None
    unit: str | None = None


BodyItem = Annotated[
    BodyText | BodyNote | BodyIngredient | BodyReference | BodyCookware | BodyTimer,
    Field(discriminator="t"),
]


class BodySection(ApiModel):
    name: str | None
    steps: list[list[BodyItem]]


class RecipeOut(RecipeSummary):
    head_commit: str | None
    parse_error_message: str | None
    front_matter: dict | None
    last_seen_at: datetime
    notes: str | None
    relink: RelinkProposal | None = None
    # The rows of the last good parse; still there when the file stopped parsing.
    ingredients: list[RecipeIngredientOut] = []  # noqa: RUF012
    pins: list[RecipePinOut] = []  # noqa: RUF012
    # The last good parse's steps (0048); null for a recipe indexed before it.
    body: list[BodySection] | None = None
    # Front matter `yield:` (#526), and the recipes that cost from this one, by
    # a recipe reference or a made-by ingredient, each once, by title, with the
    # number of their lines that do.
    makes: MakesOut | None = None
    used_in: list[RecipeLink] = []  # noqa: RUF012
    used_lines: int = 0


class IngredientRecipeUse(ApiModel):
    """One recipe using an ingredient, with each line's quantity as written."""

    id: uuid.UUID
    title: str
    path: str
    status: RecipeStatus
    quantities: list[str | None]


class IngredientRecipesOut(ApiModel):
    """The hub's "Used in" card (criterion 33): ordered by title, with the count."""

    items: list[IngredientRecipeUse]
    total: int


class StatusCounts(ApiModel):
    ok: int = 0
    parse_error: int = 0
    missing: int = 0


class RecipesStatus(ApiModel):
    """Whether a repository is mounted, what the index holds, and when it last ran."""

    mount: MountState
    mounted: bool
    head_commit: str | None
    counts: StatusCounts
    total: int
    last_scan_at: datetime | None


class MissingRecipeRef(ApiModel):
    referrer_path: str
    ref_path: str
    target: str | None  # null when the path leaves the recipes root


class ScanOut(ApiModel):
    """What one scan changed."""

    mounted: bool
    scanned_at: datetime
    files: int
    settling: int
    created: int
    updated: int
    moved: int
    missing: int
    parse_errors: int  # among the files created, updated or moved by this scan
    proposals: int
    # Recipe references whose target isn't there (#526): `kerp recipes rescan`
    # prints each with its referring file.
    missing_refs: list[MissingRecipeRef] = Field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.created + self.updated + self.moved + self.missing


class RelinkIn(ApiModel):
    target_id: uuid.UUID


# --- 3C: the resolve queue, decisions and pins -------------------------------------

# The cascade's tiers in order (07, 3C; VS2). The page badges each proposal by
# its tier; a model's guess gets the squash outline (UI-7.16).
ProposalTier = Literal["standard", "similar", "prep", "usda", "model"]


class ResolveProposal(ApiModel):
    """One suggestion for an unmatched name, badged by the cascade tier that made it.

    ``standard``: an exact standard-list entry; ``ingredient_id`` is the catalog
    ingredient already made from it, or null when choosing it creates one
    (``standard_key``). ``similar``: a ranked trigram match, never auto-applied.
    ``prep``: the name without its prep words found an ingredient or a standard
    entry exactly; ``note`` holds the stripped words, which the decision sends
    back so they move into each line's note. ``usda``: a FoodData Central food
    to create an ingredient from (``fdc_id``, sent back with the new
    ingredient). ``model``: the local model's pick from a shortlist, always one
    of the catalog's or the standard list's names.
    """

    tier: ProposalTier
    name: str
    ingredient_id: uuid.UUID | None = None
    standard_key: str | None = None
    category: str | None = None
    matched_spelling: str | None = None
    # A standard entry named by its own name, plural or a listed spelling, not by a
    # head and qualifiers: safe to accept with others in one click.
    exact: bool = False
    note: str | None = None
    fdc_id: int | None = None
    fdc_description: str | None = None


class ResolveRecipe(ApiModel):
    id: uuid.UUID
    title: str
    path: str


class ResolveName(ApiModel):
    """One unmatched name across every recipe that uses it (criterion 15)."""

    name_norm: str
    raw_names: list[str]
    recipes: list[ResolveRecipe]
    line_count: int
    proposals: list[ResolveProposal]


class ResolveQueueOut(ApiModel):
    items: list[ResolveName]
    names: int
    recipes: int
    # Whether "Ask the model" can do anything: a local model is configured (UI-7.16).
    model_configured: bool = False


class ResolveExactOut(ApiModel):
    """Names settled by "Accept exact matches", and how many names are still queued."""

    resolved: int
    remaining: int


class ResolveDecisionIn(ApiModel):
    """Exactly one of: an ingredient, a new ingredient to create, or ignore.

    ``note`` is the prep tier's stripped words, sent back from its proposal:
    they are prepended to each affected line's note ("minced; for the sauce").
    ``fdc_id`` is the USDA tier's food, sent back with ``ingredient``: the new
    ingredient gets it as its preferred reference, so the USDA review page then
    offers its densities and measures.
    """

    name_norm: str = Field(min_length=1, max_length=500)
    ingredient_id: uuid.UUID | None = None
    ingredient: IngredientCreate | None = None
    ignore: bool = False
    note: str | None = Field(default=None, max_length=200)
    fdc_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_choice(self) -> ResolveDecisionIn:
        chosen = sum((self.ingredient_id is not None, self.ingredient is not None, self.ignore))
        if chosen != 1:
            raise ValueError("give exactly one of ingredient_id, ingredient or ignore")
        if self.note is not None:
            self.note = " ".join(self.note.split()) or None
        if self.note is not None and self.ignore:
            raise ValueError("a note goes with an ingredient, not with ignore")
        if self.fdc_id is not None and self.ingredient is None:
            raise ValueError("fdc_id goes with a new ingredient")
        return self


class ResolveAskIn(ApiModel):
    """Ask the later tiers, the local model included, about one queued name."""

    name_norm: str = Field(min_length=1, max_length=500)


class ResolveAskOut(ApiModel):
    name_norm: str
    proposals: list[ResolveProposal]
    model_asked: bool  # False when no model is configured or the earlier tiers left no room


class ResolveDecisionOut(ApiModel):
    name_norm: str
    action: Literal["matched", "created", "ignored"]
    ingredient: IngredientSummary | None
    lines: int  # lines resolved by this decision, across every recipe
    recipes: int
    remaining: int  # names still in the queue


class RecipePinIn(ApiModel):
    product_id: uuid.UUID


# --- 3D: cost snapshots ------------------------------------------------------------------

# "offers" (this week's, 18 X2) is the per-kitchen view's only: it needs home_base_id.
CostBasis = Literal["latest", "average", "cheapest", "offers"]
CostLineStatus = Literal["priced", "posted", "unpriced", "unconvertible", "unmapped", "negligible"]
YieldMode = Literal["auto", "as_purchased", "edible"]


class CostPriceUsed(ApiModel):
    """The unit price a line was costed at, shown per lb, oz, fl oz or each (UI-3.10a).

    For the ``average`` basis the price is the window's mean and the product,
    location and date are those of the latest price in the window.
    """

    norm_unit_price: DecimalStr
    norm_unit: str
    display_unit_price: DecimalStr | None
    display_unit: str | None
    product_id: uuid.UUID | None
    product_name: str | None
    brand: str | None
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    location_id: uuid.UUID | None
    location_name: str | None
    observation_id: uuid.UUID | None
    observed_at: datetime | None
    stale: bool
    # This week's offer priced it (the "offers" basis); an estimate when the offer
    # printed no size and the product's stood in (X2).
    flyer_offer_id: uuid.UUID | None = None
    estimate: bool = False


class CostQuantity(ApiModel):
    """The line's as-purchased quantity in the canonical unit and as displayed."""

    canonical_qty: DecimalStr
    canonical_qty_high: DecimalStr | None
    canonical_unit: str
    display_qty: DecimalStr | None
    display_qty_high: DecimalStr | None
    display_unit: str | None


class UsdaLineOffer(ApiModel):
    """USDA's value that would convert an unconvertible line; "Use it" accepts it
    through ``POST /usda/review/{ingredient_id}`` with ``mark_reviewed: false``."""

    ingredient_id: uuid.UUID
    kind: Literal["density", "measure"]
    density_portion_id: uuid.UUID | None
    measure_label: str | None
    text: str  # "1 tsp = 2.3 g"


class CostLineHint(ApiModel):
    """Why an unpriced line has no price though one is in sight (#522, #523, #525).

    ``posted_other_product``: the line is pinned, the pin has no posted price and
    ``product_name`` has. ``posted_outside_kitchen``: in a kitchen's view, the
    only posted price is at ``store_name``, outside it. ``pin_moved``: the pinned
    ``product_name`` is now filed under ``ingredient_name``.
    """

    kind: Literal["posted_other_product", "posted_outside_kitchen", "pin_moved"]
    product_name: str | None = None
    store_name: str | None = None
    ingredient_name: str | None = None


class SourceCounts(ApiModel):
    priced: int
    total: int


class CycleHop(ApiModel):
    """One recipe on a cycle and how it reaches the next: through a recipe
    reference in its file, or through ``via_ingredient``, an ingredient it uses
    that the next recipe makes."""

    recipe: RecipeLink
    via_ingredient: IngredientSummary | None = None


class CostLineSourceOut(ApiModel):
    """The recipe a line costs from (#526), and what its message needs.

    ``target_path`` is where a recipe reference looked (null when it leaves the
    recipes root); ``suggestion`` a recipe of the same base name elsewhere.
    ``counts`` are the source's own priced and total lines (``recipe_unpriced``),
    and ``cycle`` the walk round for ``cycle``.
    """

    recipe: RecipeLink | None = None
    makes: MakesOut | None = None
    target_path: str | None = None
    suggestion: RecipeLink | None = None
    counts: SourceCounts | None = None
    cycle: list[CycleHop] = []  # noqa: RUF012


class CostLineOut(ApiModel):
    id: uuid.UUID
    line: RecipeIngredientOut
    yield_mode: YieldMode
    pinned: bool
    status: CostLineStatus
    failure_code: str | None = Field(
        description="Why the line has no cost, or a cost from a recipe failed: a conversion "
        "code (no_density, unknown_measure, no_pack, no_qty, no_bunch_weight) or a recipe "
        "code (no_yield, bad_yield, yield_unit, no_recipe, cycle, recipe_unpriced, "
        "recipe_unreadable). Each is described in docs/spec/07-phase-3-recipes-and-costing.md, 3D, "
        "under its own anchor (#failure-no-yield and so on)."
    )
    quantity: CostQuantity | None
    yield_applied: DecimalStr | None
    yield_assumed: bool  # grossed up at 100% because the ingredient has no yield yet
    bridge_kind: str | None
    bridge_confirmed: bool | None
    price: CostPriceUsed | None
    consumed_cost: DecimalStr | None
    consumed_cost_high: DecimalStr | None
    basket_cost: DecimalStr | None
    basket_cost_high: DecimalStr | None
    packs: DecimalStr | None
    packs_high: DecimalStr | None
    usda_offer: UsdaLineOffer | None = None
    hint: CostLineHint | None = None
    # Costed from a recipe (#526): which, whether it is incomplete, and the basis
    # it was costed under when not the table's ("latest" for a kitchen or offers).
    source_recipe: RecipeLink | None = None
    source_partial: bool = False
    source_basis: str | None = None
    source_counts: SourceCounts | None = None
    source: CostLineSourceOut | None = None


class CostTotals(ApiModel):
    """The counted totals; lines on a posted price are in the ``posted_*`` twins
    only, and ``per_serving`` is counted only (07, 3D)."""

    consumed_cost: DecimalStr | None
    consumed_cost_high: DecimalStr | None
    basket_cost: DecimalStr | None
    basket_cost_high: DecimalStr | None
    per_serving: DecimalStr | None
    posted_consumed_cost: DecimalStr | None = None
    posted_consumed_cost_high: DecimalStr | None = None
    posted_basket_cost: DecimalStr | None = None
    posted_basket_cost_high: DecimalStr | None = None


class CostCompleteness(ApiModel):
    lines_total: int
    lines_priced: int
    lines_unpriced: int
    lines_unconvertible: int
    lines_unmapped: int
    lines_negligible: int
    lines_posted: int = 0  # not in lines_priced
    # Lines costed from a recipe that is itself incomplete (#526): counted in
    # lines_priced or lines_posted, and a recipe with any is not complete.
    lines_partial: int = 0


class RecipeCostOut(ApiModel):
    id: uuid.UUID
    recipe_id: uuid.UUID
    basis: CostBasis
    window_days: int | None
    min_quality: int | None
    content_hash: str
    head_commit: str | None
    provisional: bool
    computed_at: datetime
    # The kitchen whose locations' prices alone were counted (17, 4A, L3): set
    # only on the on-demand view, which is never snapshotted; null otherwise.
    home_base_id: uuid.UUID | None = None
    stale_after_days: int
    totals: CostTotals
    completeness: CostCompleteness
    unconfirmed_share: DecimalStr
    # The posted consumed cost's share of counted plus posted, 0–1.
    posted_share: DecimalStr = Decimal(0)
    # Counted cost per display unit of what the recipe makes (#526).
    makes_unit_cost: MakesUnitCost | None = None
    lines: list[CostLineOut]


class CostHistoryItem(ApiModel):
    id: uuid.UUID
    content_hash: str
    head_commit: str | None
    computed_at: datetime
    window_days: int | None
    min_quality: int | None
    totals: CostTotals
    lines_priced: int
    lines_total: int
    lines_posted: int = 0


class CostHistoryOut(ApiModel):
    basis: CostBasis
    items: list[CostHistoryItem]


class RecipeCostSummary(ApiModel):
    """The latest-basis snapshot of a recipe's current content, for the list."""

    consumed_cost: DecimalStr | None
    consumed_cost_high: DecimalStr | None
    basket_cost: DecimalStr | None
    basket_cost_high: DecimalStr | None
    per_serving: DecimalStr | None
    posted_consumed_cost: DecimalStr | None = None
    posted_consumed_cost_high: DecimalStr | None = None
    posted_basket_cost: DecimalStr | None = None
    posted_basket_cost_high: DecimalStr | None = None
    posted_share: DecimalStr = Decimal(0)
    lines_priced: int
    lines_total: int
    lines_negligible: int  # left out of completeness (07, criterion 16)
    lines_posted: int = 0  # not in lines_priced, so a recipe with one is incomplete
    lines_partial: int = 0  # costed from an incomplete recipe (#526): incomplete too
    # Some line rests on a posted price older than stale_after_days ("some stale").
    posted_stale: bool = False
    provisional: bool
    computed_at: datetime

    @property
    def complete(self) -> bool:
        """Every line that counts is priced, and none from an incomplete recipe;
        negligible lines don't count."""
        return (
            self.lines_priced + self.lines_negligible >= self.lines_total
            and self.lines_partial == 0
        )


class RecipeListItem(RecipeSummary):
    cost: RecipeCostSummary | None = None


class RecipeList(ApiModel):
    items: list[RecipeListItem]
