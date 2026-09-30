"""Linking ingredients to the standard list, and merging them (03, 1G)."""

from __future__ import annotations

import uuid

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import CanonicalUnit, Categorized


class StandardEntryOut(Categorized):
    key: str
    name: str
    canonical_unit: CanonicalUnit
    fdc_id: int | None = None


class StandardEntryList(ApiModel):
    items: list[StandardEntryOut]


class LinkSuggestion(StandardEntryOut):
    # The USDA food the entry refers to, when USDA data is loaded.
    usda_description: str | None = None


class LinkOther(ApiModel):
    id: uuid.UUID
    name: str
    canonical_unit: CanonicalUnit
    products: int


class LinkRowOut(Categorized):
    id: uuid.UUID
    name: str
    canonical_unit: CanonicalUnit
    products: int
    suggestion: LinkSuggestion | None
    # Another active ingredient already has the suggested name: linking merges.
    conflict: LinkOther | None


class LinkPageOut(ApiModel):
    to_review: list[LinkRowOut]
    skipped: list[LinkRowOut]


class LinkSummaryOut(ApiModel):
    to_review: int
    skipped: int


class LinkIn(ApiModel):
    standard_key: str = Field(min_length=1, max_length=120)


class RenameIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)


class MergeTarget(ApiModel):
    survivor_id: uuid.UUID
    loser_id: uuid.UUID
    # Exactly one: the standard entry a Link named, or the name a Rename typed.
    standard_key: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _one_target(self):
        if (self.standard_key is None) == (self.name is None):
            raise ValueError("give standard_key or name, not both")
        return self


class MergeIn(MergeTarget):
    # Labels of the loser's measures to copy to the survivor (O10).
    copy_measures: list[str] = []


class MergeMeasureOut(ApiModel):
    label: str
    canonical_qty: DecimalStr
    copyable: bool
    suggested: bool


class MergeOut(ApiModel):
    survivor_id: uuid.UUID
    loser_id: uuid.UUID
    target_name: str
    products_moving: int
    unit_from: CanonicalUnit
    unit_to: CanonicalUnit
    prices_needing_bridge: int
    measures: list[MergeMeasureOut]
