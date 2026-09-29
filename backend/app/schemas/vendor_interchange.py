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
    key: str
    name: str
    lat: Coordinate
    lon: Coordinate
    address: str | None = None
    phone: str | None = None
    opening_hours: str | None = None
    osm: OsmRef | None = None
    parent: str | None = None  # another location's key, for a stall
    sources: dict[str, FieldSourceEntry] = Field(default_factory=dict)


class HouseholdLocation(_Strict):
    id: str
    home_base: str | None = None  # a home base's name; never created by import
    stop_overhead_min: int | None = None
    active: bool
    publishable: bool
    receipt_identifiers: list[str] = Field(default_factory=list)


class HouseholdVendor(_Strict):
    id: str
    notes: str | None = None
    active: bool
    locations: dict[str, HouseholdLocation] = Field(default_factory=dict)


class VendorEntry(_Strict):
    key: str
    name: str
    kind: Literal["chain", "independent", "market", "stand"]
    price_scope: Literal["chain", "location"]
    website: str | None = None
    brand: str | None = None
    wikidata: str | None = None
    sources: dict[str, FieldSourceEntry] = Field(default_factory=dict)
    locations: list[LocationEntry] = Field(default_factory=list)
    household: HouseholdVendor | None = None


class VendorFile(_Strict):
    format: Literal["kitchen-erp-vendors/1"] = FORMAT
    license: Literal["ODbL-1.0"] = LICENSE
    source: FileSource
    vendors: list[VendorEntry]
