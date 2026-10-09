"""Recipes (07, Phase 3): the indexed repository as the API shows it (3A, 3B)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from app.schemas.base import ApiModel, DecimalStr

RecipeStatus = Literal["ok", "parse_error", "missing"]
MountState = Literal["mounted", "missing", "empty", "no_cook_files"]
QtyKind = Literal["number", "range", "text", "none"]
Resolution = Literal["alias", "manual", "unmatched", "negligible"]


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


class RecipeOut(RecipeSummary):
    head_commit: str | None
    parse_error_message: str | None
    front_matter: dict | None
    last_seen_at: datetime
    notes: str | None
    relink: RelinkProposal | None = None
    # The rows of the last good parse; still there when the file stopped parsing.
    ingredients: list[RecipeIngredientOut] = []


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
