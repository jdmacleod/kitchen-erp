from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr

CanonicalUnit = Literal["g", "ml", "each"]
BridgeSource = Literal["usda", "label", "measured", "llm", "manual"]
Perishability = Literal["shelf_stable", "refrigerated", "fresh"]


# --- measures ---------------------------------------------------------------


class MeasureCreate(ApiModel):
    label: str = Field(min_length=1, max_length=100)
    canonical_qty: Decimal = Field(gt=0)
    source: BridgeSource = "manual"
    confirmed: bool = False


class MeasureUpdate(ApiModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    canonical_qty: Decimal | None = Field(default=None, gt=0)
    source: BridgeSource | None = None


class MeasureOut(ApiModel):
    id: uuid.UUID
    ingredient_id: uuid.UUID
    label: str
    canonical_qty: DecimalStr
    source: BridgeSource
    confirmed: bool
    created_at: datetime
    updated_at: datetime


# --- ingredients ------------------------------------------------------------


class _DensityPair(ApiModel):
    density_g_per_ml: Decimal | None = Field(default=None, gt=0)
    density_source: BridgeSource | None = None

    @model_validator(mode="after")
    def _pair(self):
        if (self.density_g_per_ml is None) != (self.density_source is None):
            raise ValueError("density_g_per_ml and density_source must be given together")
        return self


class IngredientCreate(_DensityPair):
    name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    canonical_unit: CanonicalUnit = "g"
    yield_pct: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    perishability: Perishability = "shelf_stable"
    notes: str | None = None


class IngredientUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    canonical_unit: CanonicalUnit | None = None
    density_g_per_ml: Decimal | None = Field(default=None, gt=0)
    density_source: BridgeSource | None = None
    clear_density: bool = False
    yield_pct: Decimal | None = Field(default=None, gt=0, le=1)
    perishability: Perishability | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _pair(self):
        if (self.density_g_per_ml is None) != (self.density_source is None):
            raise ValueError("density_g_per_ml and density_source must be given together")
        if self.clear_density and self.density_g_per_ml is not None:
            raise ValueError("clear_density cannot be combined with a new density")
        return self


class IngredientSummary(ApiModel):
    id: uuid.UUID
    name: str
    canonical_unit: CanonicalUnit
    active: bool


class IngredientOut(ApiModel):
    id: uuid.UUID
    name: str
    category: str | None
    canonical_unit: CanonicalUnit
    density_g_per_ml: DecimalStr | None
    density_source: BridgeSource | None
    density_confirmed: bool
    yield_pct: DecimalStr
    perishability: Perishability
    active: bool
    notes: str | None
    measures: list[MeasureOut] = []
    created_at: datetime
    updated_at: datetime


class IngredientList(ApiModel):
    items: list[IngredientOut]
    next_cursor: str | None = None


# --- products ---------------------------------------------------------------


class _PackPair(ApiModel):
    pack_qty: Decimal | None = Field(default=None, gt=0)
    pack_unit: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _pair(self):
        if (self.pack_qty is None) != (self.pack_unit is None):
            raise ValueError("pack_qty and pack_unit must be given together, or neither")
        return self


class ProductCreate(_PackPair):
    ingredient_id: uuid.UUID | None = None
    ingredient: IngredientCreate | None = None
    brand: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    barcode: str | None = Field(default=None, min_length=4, max_length=32)
    quality_rating: int | None = Field(default=None, ge=1, le=5)
    exclusive_vendor_id: uuid.UUID | None = None
    density_override: Decimal | None = Field(default=None, gt=0)
    density_override_source: BridgeSource | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _ingredient(self):
        if (self.ingredient_id is None) == (self.ingredient is None):
            raise ValueError("give exactly one of ingredient_id or ingredient")
        if (self.density_override is None) != (self.density_override_source is None):
            raise ValueError("density_override and density_override_source must be given together")
        return self


class ProductUpdate(ApiModel):
    ingredient_id: uuid.UUID | None = None
    brand: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    pack_qty: Decimal | None = Field(default=None, gt=0)
    pack_unit: str | None = Field(default=None, max_length=16)
    clear_pack: bool = False
    barcode: str | None = Field(default=None, min_length=4, max_length=32)
    clear_barcode: bool = False
    quality_rating: int | None = Field(default=None, ge=1, le=5)
    exclusive_vendor_id: uuid.UUID | None = None
    clear_exclusive_vendor: bool = False
    density_override: Decimal | None = Field(default=None, gt=0)
    density_override_source: BridgeSource | None = None
    clear_density_override: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def _pairs(self):
        if (self.pack_qty is None) != (self.pack_unit is None):
            raise ValueError("pack_qty and pack_unit must be given together, or neither")
        if (self.density_override is None) != (self.density_override_source is None):
            raise ValueError("density_override and density_override_source must be given together")
        return self


class ProductOut(ApiModel):
    id: uuid.UUID
    ingredient: IngredientSummary
    brand: str | None
    name: str
    pack_qty: DecimalStr | None
    pack_unit: str | None
    barcode: str | None
    quality_rating: int | None
    exclusive_vendor_id: uuid.UUID | None
    density_override: DecimalStr | None
    density_override_source: BridgeSource | None
    density_override_confirmed: bool
    active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ProductList(ApiModel):
    items: list[ProductOut]
    next_cursor: str | None = None


class SearchHit(ApiModel):
    id: uuid.UUID
    name: str
    brand: str | None
    barcode: str | None
    pack_qty: DecimalStr | None
    pack_unit: str | None
    quality_rating: int | None
    ingredient: IngredientSummary
    match: Literal["barcode", "name", "brand", "ingredient"]
    score: DecimalStr


class SearchOut(ApiModel):
    items: list[SearchHit]


# --- conversion bench -------------------------------------------------------


class ConvertIn(ApiModel):
    qty: Decimal | None = None
    unit: str = Field(min_length=1, max_length=100)
    product_id: uuid.UUID | None = None


class ProvenanceOut(ApiModel):
    bridge_kind: Literal["none", "density", "density_override", "measure", "pack"]
    source: str | None = None
    confirmed: bool | None = None
    detail: str | None = None
    via: ProvenanceOut | None = None
    rests_on_unconfirmed: bool


class ConvertOut(ApiModel):
    ok: bool
    qty: DecimalStr | None = None
    unit: str | None = None
    provenance: ProvenanceOut | None = None
    failure_code: str | None = None
    message: str | None = None
    version: str


# --- USDA suggestions -------------------------------------------------------


class DensitySuggestion(ApiModel):
    density_g_per_ml: DecimalStr
    from_portion: str


class MeasureSuggestion(ApiModel):
    label: str
    canonical_qty_g: DecimalStr
    from_portion: str


class UsdaSuggestion(ApiModel):
    fdc_id: int
    description: str
    similarity: DecimalStr
    densities: list[DensitySuggestion]
    measures: list[MeasureSuggestion]


class UsdaSuggestionList(ApiModel):
    items: list[UsdaSuggestion]
    loaded: bool
