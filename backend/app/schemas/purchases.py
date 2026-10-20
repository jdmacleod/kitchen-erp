from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.catalog.supplies import SupplyCategory
from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import Categorized, IngredientCreate, IngredientMatch, ProductCreate

ObservationSource = Literal["receipt", "manual", "shelf", "import", "listing"]
NormStatus = Literal["ok", "no_density", "unknown_measure", "no_pack", "no_qty", "no_bunch_weight"]
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
    # As shown (issue 245): per lb, oz or fl oz (or kg, L), from the server.
    display_unit_price: DecimalStr | None = None
    display_unit: str | None = None
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
    # Issue 381: the regular price when the receipt's row proves a saving already in
    # `price`, which stays what was paid; null for every other price.
    regular_price: DecimalStr | None = None
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
    # "no density" for an ingredient in g whose products are all sold by volume:
    # measuring it in ml would bridge it without a density (issue 358).
    switch_to_ml: bool = False


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
    # The OCR row the reading matched this line to; the model's raw_text can leave
    # out a row's codes and prices. Null when no row matched, or entered by hand.
    printed_text: str | None = None
    # A saving the receipt prints that its item's price already includes: shown,
    # counted as nothing (line_total is 0).
    in_price_amount: DecimalStr | None = None
    # Words of an item line on a draft one edit from a catalog word, as
    # {word, maybe} (2S): OCR's misreadings, offered for a person to fix.
    spelling: list[dict[str, str]] = []  # noqa: RUF012
    suggestions: list[dict] = []  # noqa: RUF012
    # Whether this line ever reached the price book, so removing it voids a
    # price. On a single purchase only; null in lists.
    recorded: bool | None = None
    # A code on the line its product could be remembered by, for this vendor
    # (04, 2K): offered as "Remember {code} for {product}", recorded on a click.
    code_offer: CodeOffer | None = None
    # Where it is kept and when it is best by (2Q); the date's source is
    # inferred from a keep time, printed on the label, or a person's.
    stored_in: Literal["room", "fridge", "freezer"] | None = None
    best_by: dt.date | None = None
    best_by_source: Literal["inferred", "printed", "person"] | None = None
    # A kitchen supply's category (2T): set exactly when resolution is "supply".
    supply_category: SupplyCategory | None = None


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


class OpenRereadRef(ApiModel):
    id: uuid.UUID
    status: Literal["pending", "running", "ready"]


class RereadState(ApiModel):
    """What "Re-read lines" can offer on a receipt purchase (04, 2S)."""

    # Whether the latest reading kept the reader's answer, so Re-check can run.
    can_recheck: bool
    open: OpenRereadRef | None = None


class PrintedChecks(ApiModel):
    """What a receipt prints about itself besides its total, when read (2S)."""

    savings_total: DecimalStr | None = None
    item_count: int | None = None
    payment: DecimalStr | None = None


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
    # With check_lines: how far lines_total is from the printed total (issue 122).
    gap: DecimalStr | None = None
    # A draft whose lines miss its printed total by a wide margin: "Needs a careful
    # look". Derived, never stored, and never a block on commit.
    held: bool = False
    status: str
    source: str
    flags: list[str]
    printed_checks: PrintedChecks | None = None
    ledger_txn_ref: str | None
    # The plan this purchase closed a stop of, when "Done here" recorded it (17, 4C).
    trip_plan_id: uuid.UUID | None = None
    lines: list[LineOut]
    created_at: datetime
    updated_at: datetime
    # Set when the purchase was removed after reaching the price book (#74).
    voided_at: datetime | None = None
    voided_by_name: str | None = None
    # The prices the removal voided, on a single voided purchase; null otherwise.
    voided_prices: int | None = None
    # Why a single voided purchase can't be restored (#210); null otherwise.
    restore_blocked: Literal["read_again"] | None = None
    # On a single purchase only (GET by id and every change that returns one);
    # null in lists, which never show them. Null on a voided purchase.
    removal: RemovalOut | None = None
    removed_line_count: int | None = None
    # On a single receipt purchase that is not voided; null otherwise (04, 2S).
    reread: RereadState | None = None
    # What its kitchen supply lines come to, less their savings (2T); null with none.
    supply_total: DecimalStr | None = None


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
    # A kitchen supply of this category (2T): spend only, no product and no price.
    supply_category: SupplyCategory | None = None
    accepted_kind: Literal["alias", "fuzzy", "llm", "similar", "code"] | None = None

    @model_validator(mode="after")
    def _one(self):
        chosen = (self.product_id, self.product, self.supply_category)
        if sum(1 for x in chosen if x is not None) + int(self.ignore) != 1:
            raise ValueError("give exactly one of product_id, product, ignore, or supply_category")
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
    # A saving the item's price already includes (2S): true keeps its amount and
    # counts it as nothing; false counts it again. Discounts only.
    in_price: bool | None = None


class LineKeepingIn(ApiModel):
    """Move a line to another place, or give its best-by date (2Q).

    ``date``: ``use_by`` is a printed use-by date and replaces the inferred one;
    ``sell_by`` is a printed sell-by date, which does not, so the inferred date
    stands; ``set`` and ``clear`` are a person's; ``infer`` goes back to the
    inferred date.
    """

    stored_in: Literal["room", "fridge", "freezer"] | None = None
    date: Literal["use_by", "sell_by", "set", "clear", "infer"] | None = None
    best_by: dt.date | None = None

    @model_validator(mode="after")
    def _dated(self):
        if self.date in ("use_by", "set") and self.best_by is None:
            raise ValueError("a use-by or set date needs best_by")
        if self.stored_in is None and self.date is None:
            raise ValueError("give stored_in, date, or both")
        return self


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


class LineRow(ApiModel):
    """One row of "Correct the lines" (#182): a line as it should now read.

    ``id`` names the saved line the row edits; a row without one is a new line.
    ``attach_to`` is the position, in the same list, of the item a discount or
    deposit belongs to.
    """

    id: uuid.UUID | None = None
    raw_text: str | None = Field(default=None, max_length=500)
    line_kind: Literal["item", "discount", "tax", "deposit", "fee"] = "item"
    qty: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=16)
    line_total: Decimal
    attach_to: int | None = Field(default=None, ge=0)


class LinesReplace(ApiModel):
    """Every line of a draft, in order: a saved line left out is removed (#182)."""

    lines: list[LineRow] = Field(max_length=400)


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


class NamingCandidate(ApiModel):
    """An existing product a naming row may be (#386)."""

    id: uuid.UUID
    name: str
    brand: str | None
    pack_qty: DecimalStr | None
    pack_unit: str | None


class QueueElsewhere(ApiModel):
    """Another vendor where the same wording waits."""

    vendor_id: uuid.UUID
    vendor_name: str
    line_count: int


class QueueGroup(ApiModel):
    vendor: VendorSummary
    raw_text_norm: str | None
    line_count: int
    lines: list[QueueLine]
    # The item code these lines carry where the vendor prints codes (04, 2K): once
    # identified, the person may remember it for the product.
    code: CodeOffer | None = None
    # The name and brand the wording suggests, as in bulk naming (2I).
    suggested_name: str | None = None
    suggested_brand: str | None = None
    # Products the wording's ingredient already has, closest first (#386).
    candidates: list[NamingCandidate] = Field(default_factory=list)
    elsewhere: list[QueueElsewhere] = Field(default_factory=list)


class QueueList(ApiModel):
    items: list[QueueGroup]


class QueueApply(ApiModel):
    vendor_id: uuid.UUID
    raw_text_norm: str
    product_id: uuid.UUID | None = None
    ignore: bool = False
    supply_category: SupplyCategory | None = None
    line_ids: list[uuid.UUID] | None = None

    @model_validator(mode="after")
    def _one(self):
        given = (self.product_id is not None) + self.ignore + (self.supply_category is not None)
        if given != 1:
            raise ValueError("give exactly one of product_id, ignore, or supply_category")
        return self


class QueueApplied(ApiModel):
    applied: int


class SupplyCategoryOut(ApiModel):
    """One kitchen supply category (2T), with what it covers."""

    key: SupplyCategory
    label: str
    examples: str


class SupplyCategoryList(ApiModel):
    items: list[SupplyCategoryOut]


class ModelNaming(ApiModel):
    """The model's suggestion for a row: asked for, answered, or failed (N2, N3)."""

    status: Literal["asking", "done", "failed"]
    name: str | None
    ingredient: IngredientMatch | None


class NameChange(ApiModel):
    """A printed word shown as another in the suggested name (#384)."""

    index: int
    printed: str
    shown: str
    source: Literal["learned", "spelling", "vocabulary"]


class NamingRow(ApiModel):
    """One waiting group with a suggested product (04, 2I)."""

    vendor: VendorSummary
    raw_text_norm: str | None
    line_count: int
    raw_text: str | None
    name: str
    # The printed words the name is built from, and each one it shows differently.
    name_words: list[str]
    name_changes: list[NameChange]
    # A known brand the wording starts with, taken off the name (#390).
    brand: str | None
    ingredient: IngredientMatch | None
    pack_qty: DecimalStr | None
    pack_unit: str | None
    model: ModelNaming | None
    # Products the suggested ingredient already has, closest first (#386).
    candidates: list[NamingCandidate]
    # When the group's latest line was bought, and whether every field came from
    # a sure source, for sorting and "Tick confident rows" (#389).
    latest_at: datetime | None
    confident: bool


class NamingList(ApiModel):
    items: list[NamingRow]


class NamingAsked(ApiModel):
    queued: int


class ForgottenWord(ApiModel):
    word: str = Field(min_length=1, max_length=40)
    expansion: str = Field(min_length=1, max_length=60)


class NameProductRow(ApiModel):
    """A row a person confirmed: its product, and the group it identifies.

    ``product_id`` uses a product that already exists instead of creating one
    (#179). Without it, a new product whose name and ingredient match an active
    product is refused with ``product_exists`` unless ``allow_duplicate`` is set.
    """

    vendor_id: uuid.UUID
    raw_text_norm: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    product_id: uuid.UUID | None = None
    allow_duplicate: bool = False
    ingredient_id: uuid.UUID | None = None
    ingredient: IngredientCreate | None = None
    pack_qty: Decimal | None = Field(default=None, gt=0)
    pack_unit: str | None = Field(default=None, max_length=16)
    # Learned expansions the person undid on this row, offered less from now on.
    forget_words: list[ForgottenWord] = Field(default_factory=list, max_length=20)

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
