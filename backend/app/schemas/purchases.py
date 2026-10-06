from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import Categorized, IngredientCreate, IngredientMatch, ProductCreate

ObservationSource = Literal["receipt", "manual", "shelf", "import", "listing"]
NormStatus = Literal["ok", "no_density", "unknown_measure", "no_pack", "no_qty"]
BridgeKind = Literal["none", "density", "density_override", "measure", "pack", "pack_count"]


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
    pack_count: int | None = None
    piece_name: str | None = None
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
    purchase_id: uuid.UUID | None = None
    observed_at: datetime
    price: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    source: ObservationSource
    # The vendor page a posted price came from (source = listing, 2L).
    listing_id: uuid.UUID | None = None
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
    # The saved line this updates; omitted for a new line. A saved line left out
    # of the request is removed (#72).
    id: uuid.UUID | None = None
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


class CodeOffer(ApiModel):
    scheme: Literal["vendor_sku", "rw_item", "plu"]
    value: str


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
    # Who, as people read it; the id alone is not something to show.
    resolved_by_name: str | None = None
    resolution_confidence: DecimalStr | None
    flags: list[str]
    observation_id: uuid.UUID | None
    raw_text_norm: str | None = None
    suggestions: list[dict] = []
    # Whether this line ever reached the price book, so removing it voids a
    # price. On a single purchase only; null in lists.
    recorded: bool | None = None
    # A code on the line its product could be remembered by, for this vendor
    # (04, 2K): offered as "Remember {code} for {product}", recorded on a click.
    code_offer: CodeOffer | None = None


class PurchaseLocationRef(ApiModel):
    id: uuid.UUID
    name: str
    vendor: VendorRef


class RemovalOut(ApiModel):
    """What removing the purchase would do, from the same rule the action uses."""

    outcome: Literal["delete", "void"]
    # Prices the removal voids (void), or 0 (delete).
    prices: int
    # Whether its receipt photo is deleted with it.
    photo: bool
    blocked: Literal["still_reading"] | None = None


class PurchaseOut(ApiModel):
    id: uuid.UUID
    vendor_location: PurchaseLocationRef | None
    receipt_document_id: uuid.UUID | None
    purchased_at: datetime
    subtotal: DecimalStr | None
    tax: DecimalStr | None
    total: DecimalStr
    computed_total: DecimalStr
    # What the lines come to beside the printed total: computed_total plus the
    # header's tax when no line carries tax, as reconciliation counts it.
    lines_total: DecimalStr
    # How far a receipt's reading can be trusted, from its lines as they are now;
    # null for a purchase entered by hand (#121).
    trust: Literal["adds_up", "check_lines", "couldnt_read"] | None = None
    # A draft whose lines miss its printed total by a wide margin: "Needs a careful
    # look". Derived, never stored, and never a block on commit.
    held: bool = False
    status: str
    source: str
    flags: list[str]
    ledger_txn_ref: str | None
    lines: list[LineOut]
    created_at: datetime
    updated_at: datetime
    # Set when the purchase was removed after reaching the price book (#74).
    voided_at: datetime | None = None
    voided_by_name: str | None = None
    # The prices the removal voided, on a single voided purchase; null otherwise.
    voided_prices: int | None = None
    # On a single purchase only (GET by id and every change that returns one);
    # null in lists, which never show them. Null on a voided purchase.
    removal: RemovalOut | None = None
    removed_line_count: int | None = None


class StoreCodeOfferOut(ApiModel):
    """A store code review may remember on the purchase's location (1F, design D10)."""

    code: str
    location_id: uuid.UUID
    location_name: str
    # The receipt line it was printed on, other long numbers masked as ••••.
    printed_line: str


class StoreCodeOfferResponse(ApiModel):
    offer: StoreCodeOfferOut | None


class RememberStoreCodeIn(ApiModel):
    code: str = Field(min_length=1, max_length=40)


class RemovedOut(ApiModel):
    outcome: Literal["delete", "void"]
    photo_deleted: bool
    # The purchase as it is now, after a void; null after a delete.
    purchase: PurchaseOut | None = None


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
    accepted_kind: Literal["alias", "fuzzy", "llm", "similar", "code"] | None = None

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


class LineMerge(ApiModel):
    """Join a line that is only a weight or count to the item it belongs to (#87)."""

    into_line_id: uuid.UUID


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
    # The item code these lines carry where the vendor prints codes (04, 2K): once
    # identified, the person may remember it for the product.
    code: CodeOffer | None = None


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


class ModelNaming(ApiModel):
    """The model's suggestion for a row: asked for, answered, or failed (N2, N3)."""

    status: Literal["asking", "done", "failed"]
    name: str | None
    ingredient: IngredientMatch | None


class NamingRow(ApiModel):
    """One waiting group with a suggested product (04, 2I)."""

    vendor: VendorSummary
    raw_text_norm: str | None
    line_count: int
    raw_text: str | None
    name: str
    ingredient: IngredientMatch | None
    pack_qty: DecimalStr | None
    pack_unit: str | None
    model: ModelNaming | None


class NamingList(ApiModel):
    items: list[NamingRow]


class NamingAsked(ApiModel):
    queued: int


class NameProductRow(ApiModel):
    """A row a person confirmed: its product, and the group it identifies.

    ``product_id`` uses a product that already exists instead of creating one
    (#179). Without it, a new product whose name and ingredient match an active
    product is refused with ``product_exists`` unless ``allow_duplicate`` is set.
    """

    vendor_id: uuid.UUID
    raw_text_norm: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    product_id: uuid.UUID | None = None
    allow_duplicate: bool = False
    ingredient_id: uuid.UUID | None = None
    ingredient: IngredientCreate | None = None
    pack_qty: Decimal | None = Field(default=None, gt=0)
    pack_unit: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _fields(self):
        if self.product_id is not None:
            return self
        if (self.ingredient_id is None) == (self.ingredient is None):
            raise ValueError("give exactly one of ingredient_id or ingredient")
        if (self.pack_qty is None) != (self.pack_unit is None):
            raise ValueError("pack_qty and pack_unit must be given together, or neither")
        return self


class NameProductsIn(ApiModel):
    rows: list[NameProductRow] = Field(min_length=1, max_length=200)


class RowError(ApiModel):
    code: str
    message: str
    # product_exists: the product with the same name and ingredient (#179).
    product: ProductRef | None = None


class NamedProduct(ApiModel):
    vendor_id: uuid.UUID
    raw_text_norm: str
    product_id: uuid.UUID | None
    applied: int
    error: RowError | None


class NameProductsOut(ApiModel):
    results: list[NamedProduct]


class RememberCodeOut(ApiModel):
    """The code now known for the line's product at this vendor."""

    scheme: Literal["vendor_sku", "rw_item", "plu"]
    value: str
    product_id: uuid.UUID
