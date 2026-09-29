"""The ``kitchen-erp-vendors/1`` file format (spec 03 §1F).

One model, written as YAML or JSON. Coordinates and every other non-integer
number are strings, so a file never carries a float. ``household`` blocks appear
only in a household-mode file; a public file holds public facts only.

The models are strict (unknown keys refused) because import (Phase 2 of 1F)
reads files through them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

FORMAT = "kitchen-erp-vendors/1"
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


class VendorFile(_Strict):
    format: Literal["kitchen-erp-vendors/1"] = FORMAT
    license: Literal["ODbL-1.0"] = LICENSE
    source: FileSource
    vendors: list[VendorEntry]


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
