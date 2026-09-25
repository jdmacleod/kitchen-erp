from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import Categorized, ProductCreate

ObservationSource = Literal["receipt", "manual", "shelf", "import"]
NormStatus = Literal["ok", "no_density", "unknown_measure", "no_pack", "no_qty"]
BridgeKind = Literal["none", "density", "density_override", "measure", "pack"]


class IngredientRef(Categorized):
    id: uuid.UUID
    name: str
    canonical_unit: str


class ProductRef(ApiModel):
    id: uuid.UUID
    name: str
    brand: str | None
    pack_qty: DecimalStr | None
    pack_unit: str | None
    ingredient: IngredientRef


class VendorRef(ApiModel):
    id: uuid.UUID
    name: str
    kind: str
    price_scope: str


class LocationRef(ApiModel):
    id: uuid.UUID
    name: str
    vendor: VendorRef


class NormOut(ApiModel):
    status: NormStatus
    canonical_qty: DecimalStr | None
    norm_unit: str | None
    norm_unit_price: DecimalStr | None
    bridge_kind: BridgeKind
    bridge_source: str | None
    bridge_confirmed: bool | None
    convert_version: str
    computed_at: datetime


class ObservationOut(ApiModel):
    id: uuid.UUID
    product: ProductRef
    vendor_location: LocationRef
    purchase_line_id: uuid.UUID | None
    observed_at: datetime
    price: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    source: ObservationSource
    voided: bool
    void_reason: str | None = None
    norm: NormOut | None
    created_at: datetime


class ObservationList(ApiModel):
    items: list[ObservationOut]
    next_cursor: str | None = None


class ObservationCreate(ApiModel):
    """A shelf price, or any observation entered directly."""

    product_id: uuid.UUID
    vendor_location_id: uuid.UUID
    price: Decimal = Field(ge=0)
    qty: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(min_length=1, max_length=16)
    is_promo: bool = False
    observed_at: datetime | None = None


class VoidIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)


class NeedsBridgeItem(ApiModel):
    ingredient: IngredientRef
    product: ProductRef
    status: NormStatus
    observation_count: int
    latest_observed_at: datetime


class NeedsBridgeList(ApiModel):
    items: list[NeedsBridgeItem]


class RecomputeOut(ApiModel):
    recomputed: int


class LineIn(ApiModel):
    product_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=16)
    unit_price: Decimal | None = Field(default=None, ge=0)
    line_total: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _one_of(self):
        if (self.unit_price is None) == (self.line_total is None):
            raise ValueError("give exactly one of unit_price or line_total")
        return self


class ManualPurchaseIn(ApiModel):
    vendor_location_id: uuid.UUID
    purchased_at: datetime
    total: Decimal | None = Field(default=None, ge=0)
    lines: list[LineIn] = Field(min_length=1)


class LineProductRef(Categorized):
    """A line's product; ``category`` and ``category_key`` are its ingredient's."""

    id: uuid.UUID
    name: str
    brand: str | None
    pack_qty: DecimalStr | None
    pack_unit: str | None


class LineOut(ApiModel):
    id: uuid.UUID
    seq: int
    raw_text: str | None
    line_kind: str
    product: LineProductRef | None
    parent_line_id: uuid.UUID | None
    qty: DecimalStr | None
    unit: str | None
    unit_price: DecimalStr | None
    line_total: DecimalStr
    resolution: str
    resolved_by: uuid.UUID | None
    resolution_confidence: DecimalStr | None
    flags: list[str]
    observation_id: uuid.UUID | None
    raw_text_norm: str | None = None
    suggestions: list[dict] = []


class PurchaseLocationRef(ApiModel):
    id: uuid.UUID
    name: str
    vendor: VendorRef


class PurchaseOut(ApiModel):
    id: uuid.UUID
    vendor_location: PurchaseLocationRef | None
    receipt_document_id: uuid.UUID | None
    purchased_at: datetime
    subtotal: DecimalStr | None
    tax: DecimalStr | None
    total: DecimalStr
    computed_total: DecimalStr
    status: str
    source: str
    flags: list[str]
    ledger_txn_ref: str | None
    lines: list[LineOut]
    created_at: datetime
    updated_at: datetime


class PurchaseList(ApiModel):
    items: list[PurchaseOut]
    next_cursor: str | None = None


class LastUnitOut(ApiModel):
    unit: str | None


# --- review (2D) ------------------------------------------------------------


class LineDecision(ApiModel):
    product_id: uuid.UUID | None = None
    ignore: bool = False
    product: ProductCreate | None = None  # create inline, then choose it
    accepted_kind: Literal["alias", "fuzzy", "llm"] | None = None

    @model_validator(mode="after")
    def _one(self):
        given = sum(1 for x in (self.product_id, self.product) if x is not None) + int(self.ignore)
        if given != 1:
            raise ValueError("give exactly one of product_id, product, or ignore")
        return self


class LineEdit(ApiModel):
    raw_text: str | None = None
    line_kind: Literal["item", "discount", "tax", "deposit", "fee"] | None = None
    qty: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=16)
    unit_price: Decimal | None = Field(default=None, ge=0)
    line_total: Decimal | None = None
    parent_line_id: uuid.UUID | None = None
    clear_parent: bool = False
    clear_qty: bool = False


class LineAdd(ApiModel):
    raw_text: str | None = None
    line_kind: Literal["item", "discount", "tax", "deposit", "fee"] = "item"
    qty: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=16)
    unit_price: Decimal | None = Field(default=None, ge=0)
    line_total: Decimal
    parent_line_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    after_seq: int | None = None


class PurchaseHeaderEdit(ApiModel):
    vendor_location_id: uuid.UUID | None = None
    purchased_at: datetime | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    ledger_txn_ref: str | None = None
    clear_ledger_txn_ref: bool = False


class QueueLine(ApiModel):
    line_id: uuid.UUID
    purchase_id: uuid.UUID
    raw_text: str | None
    purchased_at: datetime
    line_total: DecimalStr
    qty: DecimalStr | None
    unit: str | None


class VendorSummary(ApiModel):
    id: uuid.UUID
    name: str


class QueueGroup(ApiModel):
    vendor: VendorSummary
    raw_text_norm: str | None
    line_count: int
    lines: list[QueueLine]


class QueueList(ApiModel):
    items: list[QueueGroup]


class QueueApply(ApiModel):
    vendor_id: uuid.UUID
    raw_text_norm: str
    product_id: uuid.UUID | None = None
    ignore: bool = False
    line_ids: list[uuid.UUID] | None = None

    @model_validator(mode="after")
    def _one(self):
        if self.ignore == (self.product_id is not None):
            raise ValueError("give a product_id or ignore, not both")
        return self


class QueueApplied(ApiModel):
    applied: int
