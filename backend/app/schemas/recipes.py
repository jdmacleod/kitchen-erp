"""Recipes (07, Phase 3): the indexed repository as the API shows it (3A, 3B),
and the resolve queue, decisions and pins (3C)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import IngredientCreate, IngredientSummary

RecipeStatus = Literal["ok", "parse_error", "missing"]
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


class RecipeList(ApiModel):
    items: list[RecipeSummary]


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
