"""Recipes (07, Phase 3): the indexed repository as the API shows it (3A, 3B),
and the resolve queue, decisions and pins (3C)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import IngredientCreate, IngredientSummary

RecipeStatus = Literal["ok", "parse_error", "missing"]
# Whether every line of the current cost is priced; a recipe with no snapshot is incomplete.
Completeness = Literal["complete", "incomplete"]
MountState = Literal["mounted", "missing", "empty", "no_cook_files"]
QtyKind = Literal["number", "range", "text", "none"]
Resolution = Literal["alias", "manual", "unmatched", "negligible", "ignored"]


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


class BodyIngredient(ApiModel):
    """An ingredient reference; ``seq`` is its ``recipe_ingredient`` row, from 1."""

    t: Literal["ingredient"]
    name: str
    qty: str | None = None
    unit: str | None = None
    note: str | None = None
    seq: int


class BodyCookware(ApiModel):
    t: Literal["cookware"]
    name: str
    qty: str | None = None


class BodyTimer(ApiModel):
    t: Literal["timer"]
    name: str | None = None
    qty: str | None = None
    unit: str | None = None


BodyItem = Annotated[BodyText | BodyIngredient | BodyCookware | BodyTimer, Field(discriminator="t")]


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
    ingredients: list[RecipeIngredientOut] = []
    pins: list[RecipePinOut] = []
    # The last good parse's steps (0047); null for a recipe indexed before it.
    body: list[BodySection] | None = None


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

CostBasis = Literal["latest", "average", "cheapest"]
CostLineStatus = Literal["priced", "unpriced", "unconvertible", "unmapped", "negligible"]
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


class CostQuantity(ApiModel):
    """The line's as-purchased quantity in the canonical unit and as displayed."""

    canonical_qty: DecimalStr
    canonical_qty_high: DecimalStr | None
    canonical_unit: str
    display_qty: DecimalStr | None
    display_qty_high: DecimalStr | None
    display_unit: str | None


class CostLineOut(ApiModel):
    id: uuid.UUID
    line: RecipeIngredientOut
    yield_mode: YieldMode
    pinned: bool
    status: CostLineStatus
    failure_code: str | None
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


class CostTotals(ApiModel):
    consumed_cost: DecimalStr | None
    consumed_cost_high: DecimalStr | None
    basket_cost: DecimalStr | None
    basket_cost_high: DecimalStr | None
    per_serving: DecimalStr | None


class CostCompleteness(ApiModel):
    lines_total: int
    lines_priced: int
    lines_unpriced: int
    lines_unconvertible: int
    lines_unmapped: int
    lines_negligible: int


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
    stale_after_days: int
    totals: CostTotals
    completeness: CostCompleteness
    unconfirmed_share: DecimalStr
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
    lines_priced: int
    lines_total: int
    provisional: bool
    computed_at: datetime


class RecipeListItem(RecipeSummary):
    cost: RecipeCostSummary | None = None


class RecipeList(ApiModel):
    items: list[RecipeListItem]
