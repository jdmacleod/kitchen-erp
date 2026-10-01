"""The ``kitchen-erp-vendors`` file formats (spec 03 §1F; /2 since 1H).

One model, written as YAML or JSON. Coordinates and every other non-integer
number are strings, so a file never carries a float. ``household`` blocks appear
only in a household-mode file; a public file holds public facts only.

The models are strict (unknown keys refused) because import (Phase 2 of 1F)
reads files through them. ``/2`` adds the vendor facts products need (1H):
storefront platform, fetch policy, weighed-item label layout, receipt code
position, and a location's storefront store id. A ``/1`` file that carries any
of them is refused, so ``/1`` readers never meet a field they don't know.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FORMAT = "kitchen-erp-vendors/1"
FORMAT_V2 = "kitchen-erp-vendors/2"
FORMATS = (FORMAT, FORMAT_V2)
LICENSE = "ODbL-1.0"

Mode = Literal["public", "household"]
Coordinate = Annotated[str, Field(pattern=r"^-?\d{1,3}(\.\d+)?$")]
VendorKey = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)]
_SLUG = r"[a-z0-9][a-z0-9-]*"
LocationKey = Annotated[str, Field(pattern=rf"^{_SLUG}/{_SLUG}$", max_length=250)]
Text = Annotated[str, Field(max_length=500)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileSource(_Strict):
    name: str
    exported_at: datetime
    mode: Mode


class FieldSourceEntry(_Strict):
    source: str
    ref: str | None = None
    checked_at: datetime | None = None


class OsmRef(_Strict):
    type: Literal["node", "way", "relation"]
    id: int = Field(ge=1)


class LocationEntry(_Strict):
    key: LocationKey
    name: Annotated[str, Field(min_length=1, max_length=200)]
    lat: Coordinate
    lon: Coordinate
    address: Text | None = None
    phone: Annotated[str, Field(max_length=40)] | None = None
    opening_hours: Annotated[str, Field(max_length=2000)] | None = None
    osm: OsmRef | None = None
    parent: LocationKey | None = None  # another location's key, for a stall
    sources: dict[str, FieldSourceEntry] = Field(default_factory=dict)
    platform_store_ref: Annotated[str, Field(min_length=1, max_length=64)] | None = None  # /2


class HouseholdLocation(_Strict):
    id: str
    home_base: str | None = None  # a home base's name; never created by import
    stop_overhead_min: Annotated[int, Field(ge=0, le=32767)] | None = None
    active: bool
    publishable: bool
    receipt_identifiers: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=50
    )


class HouseholdVendor(_Strict):
    id: str
    notes: str | None = None
    active: bool
    locations: dict[str, HouseholdLocation] = Field(default_factory=dict)


class RwLayoutEntry(_Strict):
    """Where the item code and price sit in a weighed-item label (positions 1-10)."""

    item_start: Annotated[int, Field(ge=1, le=10)] = 1
    item_len: Annotated[int, Field(ge=1, le=10)] = 5
    price_start: Annotated[int, Field(ge=1, le=10)] = 6
    price_len: Annotated[int, Field(ge=1, le=10)] = 5
    price_kind: Literal["price_cents", "weight_hundredths_lb", "none"] = "price_cents"

    @model_validator(mode="after")
    def _inside(self):
        for start, length in ((self.item_start, self.item_len), (self.price_start, self.price_len)):
            if start + length > 11:
                raise ValueError("a field must end before the check digit")
        return self


class CodePositionEntry(_Strict):
    """Where a vendor's receipts print item codes (04, 2K)."""

    kind: Literal["leading_token"]
    min_len: Annotated[int, Field(ge=3, le=14)]
    max_len: Annotated[int, Field(ge=3, le=14)]

    @model_validator(mode="after")
    def _range(self):
        if self.min_len > self.max_len:
            raise ValueError("min_len must not exceed max_len")
        return self


class VendorEntry(_Strict):
    key: VendorKey
    name: Annotated[str, Field(min_length=1, max_length=200)]
    kind: Literal["chain", "independent", "market", "stand"]
    price_scope: Literal["chain", "location"]
    website: Text | None = None
    brand: Annotated[str, Field(max_length=200)] | None = None
    wikidata: Annotated[str, Field(pattern=r"^Q[0-9]+$", max_length=20)] | None = None
    sources: dict[str, FieldSourceEntry] = Field(default_factory=dict)
    locations: list[LocationEntry] = Field(default_factory=list)
    household: HouseholdVendor | None = None
    # /2 (1H): absent means "not given", never "clear".
    platform: Annotated[str, Field(pattern=r"^[a-z0-9_]+$", max_length=64)] | None = None
    fetch_policy: Literal["server_fetch", "capture_only", "none"] | None = None
    rw_layout: RwLayoutEntry | None = None
    code_position: CodePositionEntry | None = None


V2_VENDOR_FIELDS = ("platform", "fetch_policy", "rw_layout", "code_position")


def uses_v2(vendors: list[VendorEntry]) -> bool:
    return any(
        any(getattr(v, f) is not None for f in V2_VENDOR_FIELDS)
        or any(loc.platform_store_ref is not None for loc in v.locations)
        for v in vendors
    )


class VendorFile(_Strict):
    format: Literal["kitchen-erp-vendors/1", "kitchen-erp-vendors/2"] = FORMAT
    license: Literal["ODbL-1.0"] = LICENSE
    source: FileSource
    vendors: list[VendorEntry]

    @model_validator(mode="after")
    def _v1_has_no_v2_fields(self):
        if self.format == FORMAT and uses_v2(self.vendors):
            raise ValueError(
                "platform, fetch_policy, rw_layout, code_position and platform_store_ref "
                f"need format {FORMAT_V2}"
            )
        return self


# --- import report ------------------------------------------------------------


class FieldChange(BaseModel):
    field: str
    old: str | None
    new: str | None


class FieldConflict(BaseModel):
    """A field a person edited: import leaves it and says what the file had."""

    field: str
    current: str | None
    file: str | None


Outcome = Literal["created", "updated", "unchanged", "conflict", "unmatched"]


class ImportItem(BaseModel):
    target: Literal["vendor", "location"]
    key: str
    name: str
    vendor_key: str | None = None  # for a location
    outcome: Outcome
    changes: list[FieldChange] = Field(default_factory=list)
    conflicts: list[FieldConflict] = Field(default_factory=list)
    reason: str | None = None  # why a row was not matched, in plain words


class ImportCounts(BaseModel):
    created: int
    updated: int
    unchanged: int
    conflicts: int
    unmatched: int


class ImportReport(BaseModel):
    dry_run: bool
    mode: Mode
    counts: ImportCounts
    items: list[ImportItem]
    # Home base names the file uses that this deployment has none of; none is created.
    unresolved_home_bases: list[str] = Field(default_factory=list)
