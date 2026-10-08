"""Product proposals (04, 2L)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field

from app.catalog.attributes import ProductKind
from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import IngredientSummary
from app.schemas.product_photos import PhotoRole, PhotoSummary, ProductPhotoOut

ProposalStatus = Literal["pending", "accepted", "rejected", "superseded"]
ProposalKind = Literal["new_product", "product_update"]
CaptureChannel = Literal["clip", "paste_url", "barcode", "photo", "helper"]


class FieldCandidateOut(ApiModel):
    value: Any
    source: str
    confidence: str | None = None
    # "helper" when the products helper brought it (2N): the badge "Lookup helper".
    via: str | None = None


class ProposalFieldOut(FieldCandidateOut):
    """The chosen value and where it came from, with every other candidate (criterion 73)."""

    alternatives: list[FieldCandidateOut] = []
    conflict: bool = False


class CaptureOut(ApiModel):
    id: uuid.UUID
    channel: CaptureChannel
    source_url: str | None
    captured_at: datetime


class ProductJobOut(ApiModel):
    id: uuid.UUID
    kind: str
    status: str
    last_error: str | None


class LocationChoice(ApiModel):
    id: uuid.UUID
    name: str


class ProposalVendor(ApiModel):
    """The page's vendor, for the meta line and the posted price's store."""

    id: uuid.UUID
    name: str
    price_scope: Literal["chain", "location"]
    locations: list[LocationChoice]
    suggested_location_id: uuid.UUID | None


class ProposalReading(ApiModel):
    """How the capture was identified: a barcode, the vision model, or text and a model."""

    path: Literal["barcode", "vision", "ocr_text", "unread", "page"]
    error: str | None = None


class LookupState(ApiModel):
    status: Literal["open", "answered", "closed"]
    created_at: datetime
    answered_at: datetime | None


class MatchCandidateOut(ApiModel):
    """A fuzzy candidate as review shows it (2P): the product as it is now, and the
    sameness verdict against this proposal. A verdict labels; it never preselects."""

    product_id: uuid.UUID
    name: str
    brand: str | None
    pack_qty: DecimalStr | None
    pack_unit: str | None
    pack_count: int | None = None
    piece_name: str | None = None
    photo: PhotoSummary | None = None
    ingredient: IngredientSummary
    score: DecimalStr
    verdict: Literal["same", "other_size", "variant", "similar"]
    reasons: list[str]
    # The identifying words only this proposal has, and only the product has.
    only_here: list[str]
    only_there: list[str]


class LookAlikeOut(ApiModel):
    """Another pending proposal the sameness rules call the same product (2P)."""

    id: uuid.UUID
    title: str | None


class ProposalOut(ApiModel):
    id: uuid.UUID
    kind: ProposalKind
    status: ProposalStatus
    product_id: uuid.UUID | None
    capture: CaptureOut | None
    fields: dict[str, ProposalFieldOut]
    match: dict[str, Any]
    # The match's fuzzy candidates, ordered by verdict then similarity (2P).
    candidates: list[MatchCandidateOut] = []
    # Other pending proposals that are likely the same product (2P).
    look_alikes: list[LookAlikeOut] = []
    listing: dict[str, Any] | None
    vendor: ProposalVendor | None = None
    price: dict[str, Any] | None
    photos: list[ProductPhotoOut]
    jobs: list[ProductJobOut]
    reading: ProposalReading | None = None
    # The latest "Look this up online" request, for its asked / overdue / answered state.
    lookup: LookupState | None = None
    decided_at: datetime | None
    result: dict[str, Any] | None
    created_at: datetime


class ProposalSummary(ApiModel):
    id: uuid.UUID
    kind: ProposalKind
    status: ProposalStatus
    title: str | None
    brand: str | None
    channel: CaptureChannel | None
    has_conflict: bool
    # Other pending proposals that are likely the same product (2P).
    look_alikes: list[uuid.UUID] = []
    created_at: datetime


class ProposalList(ApiModel):
    items: list[ProposalSummary]
    counts: dict[str, int]


class ProposalEditIn(ApiModel):
    """A person's values, by field; they win over every other source."""

    edits: dict[str, Any] = Field(min_length=1)


class PriceIn(ApiModel):
    """The reviewer's posted price: what was paid for how much (0.69 for 1 lb)."""

    amount: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]
    qty: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    unit: Annotated[str, Field(min_length=1, max_length=16)] = "each"


class AcceptIn(ApiModel):
    action: Literal["new", "update"]
    product_id: uuid.UUID | None = None
    ingredient_id: uuid.UUID | None = None
    kind: ProductKind | None = None
    edits: dict[str, Any] = {}
    record_price: bool = False
    # Overrides the page's price and what it is for; it also settles a price conflict.
    price: PriceIn | None = None
    vendor_location_id: uuid.UUID | None = None
    main_photo_id: uuid.UUID | None = None
    photo_roles: dict[uuid.UUID, PhotoRole] = {}
    hidden_photo_ids: list[uuid.UUID] = []
