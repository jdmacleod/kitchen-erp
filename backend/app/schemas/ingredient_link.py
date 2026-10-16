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
    copy_measures: list[str] = []  # noqa: RUF012


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
    # Preselected: the cup portion, else the largest volume portion.
    suggested: bool


class MeasureOfferOut(ApiModel):
    label: str
    canonical_qty: DecimalStr
    from_portion: str
    # Ticked by default: the label starts with a volume unit or a plain size word.
    suggested: bool


class UsdaReviewGroupOut(ApiModel):
    ingredient_id: uuid.UUID
    name: str
    canonical_unit: CanonicalUnit
    fdc_id: int
    usda_description: str | None
    has_density: bool
    densities: list[DensityOfferOut]
    measures: list[MeasureOfferOut]
    # How far apart the densities are, in percent, when more than 5%.
    density_spread_pct: int | None


class UsdaReviewOut(ApiModel):
    loaded: bool
    release_date: date | None
    groups: list[UsdaReviewGroupOut]


class UsdaDecisionIn(ApiModel):
    density_portion_id: uuid.UUID | None = None
    measures: list[str] = []  # noqa: RUF012
    skip: bool = False
    # After a 409 density_exists: replace the density set since the list was read.
    replace_density: bool = False


class UsdaRelinkIn(ApiModel):
    """The USDA food a person chose for the ingredient; null leaves it with none."""

    fdc_id: int | None = Field(gt=0)


class UsdaReferenceOut(ApiModel):
    """An ingredient's preferred USDA food and its refuse (07, 3D; issue 282)."""

    fdc_id: int
    description: str
    # Inedible share as bought, in percent; null when USDA gives none.
    refuse_pct: DecimalStr | None
    # 1 − refuse / 100, as the fraction ``ingredient.yield_pct`` stores.
    yield_pct: DecimalStr | None


class IngredientUsdaOut(ApiModel):
    """Null ``reference`` when the ingredient has no preferred USDA food in the loaded release."""

    reference: UsdaReferenceOut | None


class UsdaDecisionOut(ApiModel):
    saved: int
    ingredient: IngredientOut


class IngredientDuplicateSide(ApiModel):
    id: uuid.UUID
    name: str
    products: int
    # Linked to the standard list: the likelier one to keep.
    standard: bool


class IngredientDuplicatePairOut(ApiModel):
    """Two ingredients that look like one food entered twice, and why (#346)."""

    a: IngredientDuplicateSide
    b: IngredientDuplicateSide
    reasons: list[str]
    # The suggested survivor: the standard-linked one, else the one with more products.
    keep_id: uuid.UUID


class IngredientDuplicateList(ApiModel):
    items: list[IngredientDuplicatePairOut]
