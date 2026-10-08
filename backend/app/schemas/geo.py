"""Request and response models for kitchens, vendors, vendor locations, and OSM adoption."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.models.geo import HomeBase, Vendor, VendorLocation
from app.schemas.base import ApiModel, DecimalStr
from app.services.geo import sources_of
from app.services.opening_hours import effective_hours

VendorKind = Literal["chain", "independent", "market", "stand"]
PriceScope = Literal["chain", "location"]
OsmType = Literal["node", "way", "relation"]

Lat = Annotated[Decimal, Field(ge=-90, le=90)]
Lon = Annotated[Decimal, Field(ge=-180, le=180)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Phone = Annotated[str, Field(max_length=40)]
Wikidata = Annotated[str, Field(pattern=r"^Q[0-9]+$", max_length=20)]
Brand = Annotated[str, Field(max_length=200)]
LinkedField = Literal["name", "address", "opening_hours", "phone", "website", "brand", "wikidata"]


class FieldSourceOut(ApiModel):
    """Where a field's current value came from. Absent when a person entered it."""

    source: str  # osm | observed | import | enriched:<tool>
    ref: str | None
    checked_at: datetime | None


# --- kitchens --------------------------------------------------------------


class HomeBaseCreate(ApiModel):
    name: Name
    lat: Lat
    lon: Lon
    label: str | None = Field(default=None, max_length=200)


class HomeBaseUpdate(ApiModel):
    name: Name | None = None
    lat: Lat | None = None
    lon: Lon | None = None
    label: str | None = Field(default=None, max_length=200)


class HomeBaseOut(ApiModel):
    id: uuid.UUID
    name: str
    lat: DecimalStr
    lon: DecimalStr
    label: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, home_base: HomeBase) -> HomeBaseOut:
        return cls(
            id=home_base.id,
            name=home_base.name,
            lat=home_base.place.lat,
            lon=home_base.place.lon,
            label=home_base.place.label,
            created_at=home_base.created_at,
        )


class HomeBaseList(ApiModel):
    items: list[HomeBaseOut]


# --- vendors -----------------------------------------------------------------


class VendorCreate(ApiModel):
    name: Name
    kind: VendorKind
    price_scope: PriceScope = "location"
    website: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    brand: Brand | None = None
    wikidata: Wikidata | None = None


class VendorUpdate(ApiModel):
    name: Name | None = None
    kind: VendorKind | None = None
    price_scope: PriceScope | None = None
    website: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    brand: Brand | None = None
    wikidata: Wikidata | None = None


class VendorRef(ApiModel):
    id: uuid.UUID
    name: str
    kind: VendorKind
    price_scope: PriceScope


class VendorOut(VendorRef):
    slug: str
    website: str | None
    brand: str | None
    wikidata: str | None
    notes: str | None
    active: bool
    created_at: datetime
    sources: dict[str, FieldSourceOut] = Field(default_factory=dict)

    @classmethod
    def from_model(cls, vendor: Vendor) -> VendorOut:
        base = VendorRef.model_validate(vendor).model_dump()
        return cls(
            **base,
            slug=vendor.slug,
            website=vendor.website,
            brand=vendor.brand,
            wikidata=vendor.wikidata,
            notes=vendor.notes,
            active=vendor.active,
            created_at=vendor.created_at,
            sources=sources_of(vendor, ("website", "brand", "wikidata")),
        )


class VendorListItem(VendorOut):
    location_count: int
    last_visit: datetime | None


class VendorList(ApiModel):
    items: list[VendorListItem]


# --- vendor locations ----------------------------------------------------------


class VendorInline(ApiModel):
    name: Name
    kind: VendorKind
    price_scope: PriceScope = "location"


class VendorLocationCreate(ApiModel):
    vendor_id: uuid.UUID | None = None
    vendor: VendorInline | None = None
    name: Name
    lat: Lat
    lon: Lon
    address: str | None = Field(default=None, max_length=500)
    phone: Phone | None = None
    publishable: bool = False
    parent_location_id: uuid.UUID | None = None
    opening_hours: str | None = None
    # Omitted: nearest kitchen. Explicit null: none. See model_fields_set.
    home_base_id: uuid.UUID | None = None
    stop_overhead_min: int | None = Field(default=None, ge=0, le=32767)
    receipt_identifiers: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=50
    )

    @model_validator(mode="after")
    def _one_vendor(self) -> VendorLocationCreate:
        if (self.vendor_id is None) == (self.vendor is None):
            raise ValueError("Provide exactly one of vendor_id or vendor.")
        return self


class VendorLocationUpdate(ApiModel):
    name: Name | None = None
    lat: Lat | None = None
    lon: Lon | None = None
    address: str | None = Field(default=None, max_length=500)
    phone: Phone | None = None
    publishable: bool | None = None
    parent_location_id: uuid.UUID | None = None
    opening_hours: str | None = None
    home_base_id: uuid.UUID | None = None
    stop_overhead_min: int | None = Field(default=None, ge=0, le=32767)
    receipt_identifiers: list[Annotated[str, Field(max_length=200)]] | None = Field(
        default=None, max_length=50
    )


class VendorLocationOut(ApiModel):
    id: uuid.UUID
    vendor: VendorRef
    name: str
    key: str
    lat: DecimalStr
    lon: DecimalStr
    address: str | None
    phone: str | None
    home_base_id: uuid.UUID | None
    parent_location_id: uuid.UUID | None
    opening_hours: str | None
    effective_opening_hours: str | None
    opening_hours_inherited: bool
    stop_overhead_min: int | None
    receipt_identifiers: list[str]
    osm_type: OsmType | None
    osm_id: int | None
    publishable: bool
    sources: dict[str, FieldSourceOut] = Field(default_factory=dict)
    active: bool
    is_open: bool | None = None  # set only when the request named an instant
    distance_m: DecimalStr | None = None  # set only when the request named a point
    created_at: datetime

    @classmethod
    def from_model(
        cls,
        location: VendorLocation,
        *,
        is_open: bool | None = None,
        distance_m: Decimal | None = None,
    ) -> VendorLocationOut:
        hours, inherited = effective_hours(location)
        return cls(
            id=location.id,
            vendor=VendorRef.model_validate(location.vendor),
            name=location.name,
            key=location.key,
            lat=location.place.lat,
            lon=location.place.lon,
            address=location.address,
            phone=location.phone,
            home_base_id=location.home_base_id,
            parent_location_id=location.parent_location_id,
            opening_hours=location.opening_hours,
            effective_opening_hours=hours,
            opening_hours_inherited=inherited,
            stop_overhead_min=location.stop_overhead_min,
            receipt_identifiers=list(location.receipt_identifiers),
            osm_type=location.osm_type,  # type: ignore[arg-type]
            osm_id=location.osm_id,
            publishable=location.publishable,
            sources=sources_of(location, ("name", "address", "opening_hours", "phone")),
            active=location.active,
            is_open=is_open,
            distance_m=distance_m,
            created_at=location.created_at,
        )


class VendorLocationDetail(VendorLocationOut):
    stalls: list[VendorLocationOut] = Field(default_factory=list)

    @classmethod
    def from_model_with_stalls(
        cls, location: VendorLocation, stalls: list[VendorLocation]
    ) -> VendorLocationDetail:
        base = VendorLocationOut.from_model(location)
        return cls(**base.model_dump(), stalls=[VendorLocationOut.from_model(s) for s in stalls])


class VendorLocationList(ApiModel):
    items: list[VendorLocationOut]


class IsOpenOut(ApiModel):
    id: uuid.UUID
    at: datetime
    is_open: bool | None
    effective_opening_hours: str | None


# --- opening hours ---------------------------------------------------------------


class OpeningHoursValidateIn(ApiModel):
    text: str = Field(max_length=2000)


class OpeningHoursValidateOut(ApiModel):
    valid: bool
    error: str | None


# --- OpenStreetMap ---------------------------------------------------------------


class OsmCandidateOut(ApiModel):
    osm_type: OsmType
    osm_id: int
    name: str | None
    kind_guess: VendorKind
    lat: DecimalStr
    lon: DecimalStr
    address: str | None
    opening_hours: str | None
    already_adopted: bool


class OsmCandidateList(ApiModel):
    items: list[OsmCandidateOut]


class OsmAdoptIn(ApiModel):
    osm_type: OsmType
    osm_id: int = Field(ge=1)
    home_base_id: uuid.UUID
    radius_m: int = Field(ge=100, le=50000)
    vendor_kind: VendorKind | None = None


class LinkedLocationRef(ApiModel):
    id: uuid.UUID
    name: str


class LinkCandidateOut(ApiModel):
    """An OSM object near a location's pin, and what linking it would change (1F)."""

    osm_type: OsmType
    osm_id: int
    name: str | None
    kind_guess: VendorKind
    address: str | None
    opening_hours: str | None
    phone: str | None
    website: str | None
    distance_m: int
    linked_to: LinkedLocationRef | None
    fills: list[LinkedField]
    keeps: list[LinkedField]  # fields a person edited, which linking leaves alone


class LinkCandidateList(ApiModel):
    items: list[LinkCandidateOut]


class OsmLinkIn(ApiModel):
    osm_type: OsmType
    osm_id: int = Field(ge=1)
    radius_m: int = Field(default=250, ge=50, le=2000)


class ExportSummaryOut(ApiModel):
    """What a public vendor export would hold, before downloading it (design D12)."""

    locations: int  # active locations of active vendors
    public: int  # of those, the ones a public export includes


class MapPlaceOut(ApiModel):
    """A named place in the deployment's own map extract (#62)."""

    name: str
    kind: Literal["town", "neighbourhood", "poi"]
    detail: str | None
    lat: DecimalStr
    lon: DecimalStr
    distance_m: int | None


class MapPlaceList(ApiModel):
    items: list[MapPlaceOut]
