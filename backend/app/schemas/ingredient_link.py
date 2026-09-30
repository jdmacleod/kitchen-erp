"""Linking ingredients to the standard list, and merging them (03, 1G)."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import CanonicalUnit, Categorized, IngredientOut


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


class DensityOfferOut(ApiModel):
    portion_id: uuid.UUID
    portion_label: str
    gram_weight: DecimalStr
    density_g_per_ml: DecimalStr


class MeasureOfferOut(ApiModel):
    label: str
    canonical_qty: DecimalStr
    from_portion: str


class UsdaReviewGroupOut(ApiModel):
    ingredient_id: uuid.UUID
    name: str
    canonical_unit: CanonicalUnit
    fdc_id: int
    usda_description: str | None
    has_density: bool
    densities: list[DensityOfferOut]
    measures: list[MeasureOfferOut]


class UsdaReviewOut(ApiModel):
    loaded: bool
    release_date: date | None
    groups: list[UsdaReviewGroupOut]


class UsdaDecisionIn(ApiModel):
    density_portion_id: uuid.UUID | None = None
    measures: list[str] = []
    skip: bool = False
    # After a 409 density_exists: replace the density set since the list was read.
    replace_density: bool = False


class UsdaDecisionOut(ApiModel):
    saved: int
    ingredient: IngredientOut
