from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, computed_field, model_validator

from app.catalog import categories, keep
from app.catalog.attributes import ProductKind
from app.catalog.categories import CategoryKey
from app.catalog.identifiers import Symbology as BarcodeSymbology
from app.catalog.keep import Place as StoragePlace
from app.catalog.perishability import Perishability
from app.schemas.base import ApiModel, DecimalStr
from app.schemas.product_photos import PhotoSummary

CanonicalUnit = Literal["g", "ml", "each"]
BridgeSource = Literal["usda", "label", "measured", "llm", "manual"]


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
    # 1G: create from the standard list. The entry supplies the name, category,
    # unit, spellings, USDA reference and measures; the fields above are ignored.
    standard_key: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=100)
    canonical_unit: CanonicalUnit = "g"
    yield_pct: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    # None: the standard entry's value, or the default for the category.
    perishability: Perishability | None = None
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
    # Whole days it keeps unopened in each place (2Q); null clears one.
    keep_room_days: int | None = Field(default=None, ge=0, le=3650)
    keep_fridge_days: int | None = Field(default=None, ge=0, le=3650)
    keep_freezer_days: int | None = Field(default=None, ge=0, le=3650)
    notes: str | None = None

    @model_validator(mode="after")
    def _pair(self):
        if (self.density_g_per_ml is None) != (self.density_source is None):
            raise ValueError("density_g_per_ml and density_source must be given together")
        if self.clear_density and self.density_g_per_ml is not None:
            raise ValueError("clear_density cannot be combined with a new density")
        return self


ReconcileState = Literal["unreviewed", "linked", "skipped", "not_applicable"]


class Categorized(ApiModel):
    """An ingredient shape that carries its category and the display key for it (D12)."""

    category: str | None

    @computed_field
    @property
    def category_key(self) -> CategoryKey | None:
        return categories.key(self.category)


class IngredientSummary(Categorized):
    id: uuid.UUID
    name: str
    canonical_unit: CanonicalUnit
    active: bool


class IngredientOut(Categorized):
    id: uuid.UUID
    name: str
    slug: str
    canonical_unit: CanonicalUnit
    density_g_per_ml: DecimalStr | None
    density_source: BridgeSource | None
    density_confirmed: bool
    yield_pct: DecimalStr
    perishability: Perishability
    keep_room_days: int | None = None
    keep_fridge_days: int | None = None
    keep_freezer_days: int | None = None
    active: bool
    notes: str | None
    measures: list[MeasureOut] = []
    # Where it stands against the standard list (1G); "linked" has its standard name.
    reconcile_state: ReconcileState = "not_applicable"
    # The ingredient this one was merged into; its page links there (#211).
    merged_into: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def stored_in(self) -> StoragePlace:
        """Where it is kept by default, from its perishability (2Q)."""
        return keep.stored_in(self.perishability)


class IngredientMatch(Categorized):
    """One ingredient search row (1G): a catalog ingredient or a standard name."""

    kind: Literal["ingredient", "standard"]
    id: uuid.UUID | None = None
    key: str | None = None
    name: str
    canonical_unit: CanonicalUnit
    active: bool = True
    # The spelling the text matched when it wasn't the name, e.g. "green onion".
    matched_spelling: str | None = None
    # The text equals the name or a spelling (ignoring case, accents, punctuation).
    exact: bool = False


class IngredientSearchOut(ApiModel):
    items: list[IngredientMatch]


class IngredientList(ApiModel):
    items: list[IngredientOut]
    next_cursor: str | None = None


# --- products ---------------------------------------------------------------


class _PackPair(ApiModel):
    pack_qty: Decimal | None = Field(default=None, gt=0)
    pack_unit: str | None = Field(default=None, max_length=16)
    # The pieces a mass or volume pack holds (19 oz, 5 links), and what one is called.
    pack_count: int | None = Field(default=None, gt=0, le=100000)
    piece_name: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def _pair(self):
        if (self.pack_qty is None) != (self.pack_unit is None):
            raise ValueError("pack_qty and pack_unit must be given together, or neither")
        if self.piece_name is not None and self.pack_count is None:
            raise ValueError("piece_name needs pack_count")
        return self


class ProductCreate(_PackPair):
    ingredient_id: uuid.UUID | None = None
    ingredient: IngredientCreate | None = None
    brand: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    barcode: str | None = Field(default=None, min_length=4, max_length=32)
    barcode_symbology: BarcodeSymbology | None = None
    kind: ProductKind | None = None
    attributes: dict[str, Any] | None = None
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
    pack_count: int | None = Field(default=None, gt=0, le=100000)
    piece_name: str | None = Field(default=None, min_length=1, max_length=32)
    # Clears the pieces (clear_pack clears them too).
    clear_pieces: bool = False
    barcode: str | None = Field(default=None, min_length=4, max_length=32)
    barcode_symbology: BarcodeSymbology | None = None
    clear_barcode: bool = False
    kind: ProductKind | None = None
    attributes: dict[str, Any] | None = None
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
    pack_count: int | None = None
    piece_name: str | None = None
    barcode: str | None
    kind: ProductKind
    attributes: dict[str, Any]
    quality_rating: int | None
    exclusive_vendor_id: uuid.UUID | None
    density_override: DecimalStr | None
    density_override_source: BridgeSource | None
    density_override_confirmed: bool
    active: bool
    notes: str | None
    # The product this one was merged into (#179); its page links there.
    merged_into: uuid.UUID | None = None
    # The main photo (1I); None shows the category placeholder.
    photo: PhotoSummary | None = None
    created_at: datetime
    updated_at: datetime


class DuplicatePairOut(ApiModel):
    """Two active products the sameness rules call the same (2P, 03)."""

    a: ProductOut
    b: ProductOut
    reasons: list[str]


class DuplicateList(ApiModel):
    items: list[DuplicatePairOut]


class DistinctIn(ApiModel):
    """ "Not the same": the pair is never offered again, in either order."""

    a: uuid.UUID
    b: uuid.UUID


class ProductMergeIn(ApiModel):
    """Merge the product in the path into ``survivor_id``, which is kept."""

    survivor_id: uuid.UUID


class ProductMergeOut(ApiModel):
    survivor_id: uuid.UUID
    loser_id: uuid.UUID
    survivor_name: str
    loser_name: str
    prices: int
    listings: int
    codes: int
    photos: int
    aliases: int
    lines: int
    survivor_pack_unit: str | None
    loser_pack_unit: str | None
    compare_unit: str
    other_dimension_prices: int
    other_dimension_units: list[str]
    prices_needing_bridge: int


class LastPaid(ApiModel):
    """The latest committed purchase of a product: what was paid, where and when."""

    price: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    vendor_id: uuid.UUID
    vendor_name: str
    purchase_id: uuid.UUID
    paid_at: datetime


class ProductListItem(ProductOut):
    last_paid: LastPaid | None = None


class ProductList(ApiModel):
    items: list[ProductListItem]
    next_cursor: str | None = None


class SearchHit(ApiModel):
    id: uuid.UUID
    name: str
    brand: str | None
    barcode: str | None
    pack_qty: DecimalStr | None
    pack_unit: str | None
    pack_count: int | None = None
    piece_name: str | None = None
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
