"""Vendor files in the ``kitchen-erp-vendors/1`` format: export (1F, Phase 1).

A household's list of the stores it visits is personal data even with every
household field removed (SECURITY.md), so there are two modes:

- ``household`` carries everything the household holds about its vendors, for
  moving between deployments or feeding a tool the household controls.
- ``public`` carries only what may be contributed: active vendors and their
  active locations that are marked publishable or linked to OpenStreetMap, never
  a stand (its pin may be someone's home), and none of notes, home bases, stop
  overheads, active flags, ids, store codes or anything from purchases.

A person reads a public file before contributing it; the application never
sends it anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.geo import HomeBase, Vendor, VendorLocation
from app.schemas.vendor_interchange import (
    FieldSourceEntry,
    FileSource,
    HouseholdLocation,
    HouseholdVendor,
    LocationEntry,
    Mode,
    OsmRef,
    VendorEntry,
    VendorFile,
)
from app.services.geo import sources_of

SOURCE_NAME = "kitchen-erp"
LOCATION_SOURCE_FIELDS = ("name", "address", "opening_hours", "phone")


def is_public(location: VendorLocation, vendor: Vendor) -> bool:
    """Whether a location may appear in a public export."""
    return (
        vendor.active
        and location.active
        and vendor.kind != "stand"
        and (location.publishable or location.osm_id is not None)
    )


def _sources(obj: Vendor | VendorLocation, names: tuple[str, ...]) -> dict[str, FieldSourceEntry]:
    return {k: FieldSourceEntry(**v) for k, v in sources_of(obj, names).items()}


def _location(location: VendorLocation, keys: dict[Any, str]) -> LocationEntry:
    return LocationEntry(
        key=location.key,
        name=location.name,
        lat=str(location.place.lat),
        lon=str(location.place.lon),
        address=location.address,
        phone=location.phone,
        opening_hours=location.opening_hours,
        osm=(
            OsmRef(type=location.osm_type, id=location.osm_id)  # type: ignore[arg-type]
            if location.osm_type is not None and location.osm_id is not None
            else None
        ),
        parent=keys.get(location.parent_location_id),
        sources=_sources(location, LOCATION_SOURCE_FIELDS),
    )


async def build(db: AsyncSession, mode: Mode, *, now: datetime | None = None) -> VendorFile:
    vendors = list(
        (
            await db.execute(
                select(Vendor)
                .options(selectinload(Vendor.locations).joinedload(VendorLocation.place))
                .order_by(Vendor.slug)
            )
        )
        .unique()
        .scalars()
    )
    home_names = {h.id: h.name for h in (await db.execute(select(HomeBase))).unique().scalars()}
    public = mode == "public"
    included: list[tuple[Vendor, list[VendorLocation]]] = []
    for vendor in vendors:
        locations = sorted(vendor.locations, key=lambda loc: loc.key)
        if public:
            locations = [loc for loc in locations if is_public(loc, vendor)]
            if not locations:
                continue  # a vendor with nothing public is not named at all
        included.append((vendor, locations))
    # A parent that is not in the file is not named either.
    keys = {loc.id: loc.key for _, locations in included for loc in locations}

    entries: list[VendorEntry] = []
    for vendor, locations in included:
        entry = VendorEntry(
            key=vendor.slug,
            name=vendor.name,
            kind=vendor.kind,  # type: ignore[arg-type]
            price_scope=vendor.price_scope,  # type: ignore[arg-type]
            website=vendor.website,
            brand=vendor.brand,
            wikidata=vendor.wikidata,
            sources=_sources(vendor, ("website",)),
            locations=[_location(loc, keys) for loc in locations],
        )
        if not public:
            entry.household = HouseholdVendor(
                id=str(vendor.id),
                notes=vendor.notes,
                active=vendor.active,
                locations={
                    loc.key: HouseholdLocation(
                        id=str(loc.id),
                        home_base=home_names.get(loc.home_base_id),
                        stop_overhead_min=loc.stop_overhead_min,
                        active=loc.active,
                        publishable=loc.publishable,
                        receipt_identifiers=list(loc.receipt_identifiers),
                    )
                    for loc in locations
                },
            )
        entries.append(entry)
    return VendorFile(
        source=FileSource(name=SOURCE_NAME, exported_at=now or datetime.now(UTC), mode=mode),
        vendors=entries,
    )


def to_document(file: VendorFile) -> dict[str, Any]:
    """Plain data, with empty optional fields left out so a file stays readable."""
    return file.model_dump(mode="json", exclude_none=True)


def render(file: VendorFile, fmt: str) -> str:
    document = to_document(file)
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    # safe_dump quotes a string that would read back as a number, so coordinates
    # stay strings in YAML as in JSON.
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)


@dataclass(frozen=True)
class ExportSummary:
    locations: int  # active locations of active vendors
    public: int  # of those, the ones a public export includes


async def summary(db: AsyncSession) -> ExportSummary:
    rows = (
        (
            await db.execute(
                select(VendorLocation, Vendor)
                .join(Vendor, Vendor.id == VendorLocation.vendor_id)
                .where(Vendor.active.is_(True), VendorLocation.active.is_(True))
            )
        )
        .unique()
        .all()
    )
    return ExportSummary(
        locations=len(rows), public=sum(1 for loc, vendor in rows if is_public(loc, vendor))
    )
