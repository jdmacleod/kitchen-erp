"""Home bases, vendors, vendor locations, and OSM adoption. Routers stay thin; rules live here.

Conventions: vendors and locations are deactivated, never deleted, because a
purchase may reference them. Home bases may be deleted while nothing points at
them. A new location's home base is the nearest one by geodesic distance unless
the caller names one or explicitly clears it. A stall (a location with a parent)
inherits its parent's opening hours while its own are null; a parent must itself
be a top-level location, so stalls do not nest.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Numeric, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.core.errors import ApiError
from app.models.geo import HomeBase, Place, Vendor, VendorLocation, point_expr
from app.services import osm
from app.services.opening_hours import OpeningHoursError, is_open_at, normalize_hours, to_household

_INTEGRITY_CODES = {
    "uq_vendor_name_lower": (409, "vendor_name_taken", "A vendor with that name already exists."),
    "uq_home_base_name": (
        409,
        "home_base_name_taken",
        "A home base with that name already exists.",
    ),
    "uq_location_osm": (409, "already_adopted", "That OpenStreetMap object is already adopted."),
    "fk_product_exclusive_vendor": (409, "vendor_in_use", "A product references this vendor."),
}


def _raise_integrity(exc: IntegrityError) -> None:
    text = str(exc.orig)
    for name, (status, code, message) in _INTEGRITY_CODES.items():
        if name in text:
            raise ApiError(status, code, message) from exc
    raise


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        _raise_integrity(exc)


def _hours(value: str | None) -> str | None:
    try:
        return normalize_hours(value)
    except OpeningHoursError as exc:
        raise ApiError(422, "invalid_opening_hours", exc.message) from None


def _clean(value: str | None, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ApiError(422, "validation_error", "A name is required.")
        return None
    stripped = value.strip()
    if required and not stripped:
        raise ApiError(422, "validation_error", "A name must not be blank.")
    return stripped or None


# --- places ------------------------------------------------------------------


def _new_place(lat: Decimal, lon: Decimal, label: str | None) -> Place:
    return Place(lat=lat, lon=lon, label=label, geom=point_expr(lat, lon))


def _move_place(place: Place, lat: Decimal | None, lon: Decimal | None) -> None:
    new_lat = place.lat if lat is None else lat
    new_lon = place.lon if lon is None else lon
    place.lat = new_lat
    place.lon = new_lon
    place.geom = point_expr(new_lat, new_lon)


# --- home bases --------------------------------------------------------------


async def _load_home_base(db: AsyncSession, home_base_id: uuid.UUID) -> HomeBase:
    result = await db.execute(
        select(HomeBase)
        .where(HomeBase.id == home_base_id)
        .execution_options(populate_existing=True)
    )
    home_base = result.unique().scalar_one_or_none()
    if home_base is None:
        raise ApiError(404, "not_found", "No such home base.")
    return home_base


async def create_home_base(
    db: AsyncSession, *, name: str, lat: Decimal, lon: Decimal, label: str | None
) -> HomeBase:
    name = _clean(name, required=True) or ""
    home_base = HomeBase(name=name, place=_new_place(lat, lon, _clean(label) or name))
    db.add(home_base)
    await _commit(db)
    return await _load_home_base(db, home_base.id)


async def list_home_bases(db: AsyncSession) -> list[HomeBase]:
    result = await db.execute(select(HomeBase).order_by(HomeBase.name))
    return list(result.unique().scalars())


async def get_home_base(db: AsyncSession, home_base_id: uuid.UUID) -> HomeBase:
    return await _load_home_base(db, home_base_id)


async def update_home_base(
    db: AsyncSession, home_base_id: uuid.UUID, changes: dict[str, Any]
) -> HomeBase:
    home_base = await _load_home_base(db, home_base_id)
    if "name" in changes:
        home_base.name = _clean(changes["name"], required=True) or ""
    if "label" in changes:
        home_base.place.label = _clean(changes["label"])
    if "lat" in changes or "lon" in changes:
        _move_place(home_base.place, changes.get("lat"), changes.get("lon"))
    await _commit(db)
    return await _load_home_base(db, home_base_id)


async def delete_home_base(db: AsyncSession, home_base_id: uuid.UUID) -> None:
    home_base = await _load_home_base(db, home_base_id)
    referenced = await db.scalar(
        select(func.count())
        .select_from(VendorLocation)
        .where(VendorLocation.home_base_id == home_base_id)
    )
    if referenced:
        raise ApiError(
            409,
            "home_base_in_use",
            "Vendor locations still point at this home base; reassign or clear them first.",
            {"locations": int(referenced)},
        )
    place = home_base.place
    await db.delete(home_base)
    await db.flush()
    await db.delete(place)
    await _commit(db)


async def nearest_home_base_id(db: AsyncSession, lat: Decimal, lon: Decimal) -> uuid.UUID | None:
    result = await db.execute(
        select(HomeBase.id)
        .join(Place, Place.id == HomeBase.place_id)
        .order_by(func.ST_Distance(Place.geom, point_expr(lat, lon)), HomeBase.name)
        .limit(1)
    )
    return result.scalar_one_or_none()


# --- vendors -----------------------------------------------------------------


async def _load_vendor(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor:
    result = await db.execute(
        select(Vendor).where(Vendor.id == vendor_id).execution_options(populate_existing=True)
    )
    vendor = result.scalar_one_or_none()
    if vendor is None:
        raise ApiError(404, "not_found", "No such vendor.")
    return vendor


async def find_vendor_by_name(db: AsyncSession, name: str) -> Vendor | None:
    result = await db.execute(select(Vendor).where(func.lower(Vendor.name) == name.strip().lower()))
    return result.scalar_one_or_none()


async def create_vendor(
    db: AsyncSession,
    *,
    name: str,
    kind: str,
    price_scope: str = "location",
    website: str | None = None,
    notes: str | None = None,
) -> Vendor:
    vendor = Vendor(
        name=_clean(name, required=True) or "",
        kind=kind,
        price_scope=price_scope,
        website=_clean(website),
        notes=notes,
        active=True,
    )
    db.add(vendor)
    await _commit(db)
    return await _load_vendor(db, vendor.id)


async def list_vendors(
    db: AsyncSession, *, q: str | None = None, include_inactive: bool = False
) -> list[Vendor]:
    stmt = select(Vendor)
    if not include_inactive:
        stmt = stmt.where(Vendor.active.is_(True))
    needle = (q or "").strip().lower()
    if needle:
        lowered = func.lower(Vendor.name)
        similarity = func.similarity(lowered, needle)
        stmt = stmt.where(
            or_(lowered.contains(needle, autoescape=True), similarity > 0.3)
        ).order_by(similarity.desc(), Vendor.name)
    else:
        stmt = stmt.order_by(Vendor.name)
    result = await db.execute(stmt)
    return list(result.scalars())


async def get_vendor(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor:
    return await _load_vendor(db, vendor_id)


async def update_vendor(db: AsyncSession, vendor_id: uuid.UUID, changes: dict[str, Any]) -> Vendor:
    vendor = await _load_vendor(db, vendor_id)
    if "name" in changes:
        vendor.name = _clean(changes["name"], required=True) or ""
    for field in ("kind", "price_scope"):
        if field in changes:
            setattr(vendor, field, changes[field])
    if "website" in changes:
        vendor.website = _clean(changes["website"])
    if "notes" in changes:
        vendor.notes = changes["notes"]
    await _commit(db)
    return await _load_vendor(db, vendor_id)


async def set_vendor_active(db: AsyncSession, vendor_id: uuid.UUID, active: bool) -> Vendor:
    vendor = await _load_vendor(db, vendor_id)
    vendor.active = active
    await _commit(db)
    return await _load_vendor(db, vendor_id)


# --- vendor locations ----------------------------------------------------------


@dataclass
class LocationRow:
    location: VendorLocation
    distance_m: Decimal | None = None
    is_open: bool | None = None


def _location_query():
    return select(VendorLocation).options(selectinload(VendorLocation.parent))


async def _load_location(db: AsyncSession, location_id: uuid.UUID) -> VendorLocation:
    result = await db.execute(
        _location_query()
        .where(VendorLocation.id == location_id)
        .execution_options(populate_existing=True)
    )
    location = result.unique().scalar_one_or_none()
    if location is None:
        raise ApiError(404, "not_found", "No such vendor location.")
    return location


async def _resolve_parent(db: AsyncSession, parent_id: uuid.UUID | None) -> VendorLocation | None:
    if parent_id is None:
        return None
    result = await db.execute(select(VendorLocation).where(VendorLocation.id == parent_id))
    parent = result.unique().scalar_one_or_none()
    if parent is None:
        raise ApiError(404, "not_found", "No such parent location.")
    if parent.parent_location_id is not None:
        raise ApiError(
            422, "parent_is_stall", "A stall cannot contain stalls; choose the market itself."
        )
    return parent


async def _has_stalls(db: AsyncSession, location_id: uuid.UUID) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(VendorLocation)
        .where(VendorLocation.parent_location_id == location_id)
    )
    return bool(count)


async def _resolve_home_base(
    db: AsyncSession, data: dict[str, Any], lat: Decimal, lon: Decimal
) -> uuid.UUID | None:
    """Omitted: nearest. Explicit null: none. Explicit id: must exist."""
    if "home_base_id" not in data:
        return await nearest_home_base_id(db, lat, lon)
    home_base_id = data["home_base_id"]
    if home_base_id is not None:
        await _load_home_base(db, home_base_id)
    return home_base_id


async def _vendor_for_create(db: AsyncSession, data: dict[str, Any]) -> Vendor:
    if data.get("vendor_id") is not None:
        return await _load_vendor(db, data["vendor_id"])
    inline = data["vendor"]
    existing = await find_vendor_by_name(db, inline["name"])
    if existing is not None:
        return existing
    vendor = Vendor(
        name=_clean(inline["name"], required=True) or "",
        kind=inline["kind"],
        price_scope=inline.get("price_scope", "location"),
        active=True,
    )
    db.add(vendor)
    return vendor


async def create_location(db: AsyncSession, data: dict[str, Any]) -> VendorLocation:
    """``data`` is the request body with unset fields absent (``exclude_unset``)."""
    name = _clean(data["name"], required=True) or ""
    lat, lon = data["lat"], data["lon"]
    vendor = await _vendor_for_create(db, data)
    parent = await _resolve_parent(db, data.get("parent_location_id"))
    hours = _hours(data.get("opening_hours"))
    home_base_id = await _resolve_home_base(db, data, lat, lon)
    location = VendorLocation(
        vendor=vendor,
        place=_new_place(lat, lon, name),
        home_base_id=home_base_id,
        parent=parent,
        name=name,
        address=_clean(data.get("address")),
        opening_hours=hours,
        stop_overhead_min=data.get("stop_overhead_min"),
        receipt_identifiers=[s.strip() for s in data.get("receipt_identifiers") or [] if s.strip()],
        active=True,
    )
    db.add(location)
    await _commit(db)
    return await _load_location(db, location.id)


def _parse_near(near: str | None) -> tuple[Decimal, Decimal] | None:
    if near is None:
        return None
    try:
        lat_text, lon_text = near.split(",", 1)
        lat, lon = Decimal(lat_text.strip()), Decimal(lon_text.strip())
    except (ValueError, InvalidOperation):
        raise ApiError(422, "validation_error", "near must be '<lat>,<lon>'.") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError(422, "validation_error", "near is outside the valid coordinate range.")
    return lat, lon


async def list_locations(
    db: AsyncSession,
    *,
    near: str | None = None,
    kind: str | None = None,
    home_base_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    open_at: datetime | None = None,
    include_inactive: bool = False,
) -> list[LocationRow]:
    point = _parse_near(near)
    stmt = (
        select(VendorLocation)
        .join(Vendor, Vendor.id == VendorLocation.vendor_id)
        .join(Place, Place.id == VendorLocation.place_id)
        .options(
            contains_eager(VendorLocation.vendor),
            contains_eager(VendorLocation.place),
            selectinload(VendorLocation.parent),
        )
    )
    if not include_inactive:
        stmt = stmt.where(VendorLocation.active.is_(True), Vendor.active.is_(True))
    if kind is not None:
        stmt = stmt.where(Vendor.kind == kind)
    if home_base_id is not None:
        stmt = stmt.where(VendorLocation.home_base_id == home_base_id)
    if vendor_id is not None:
        stmt = stmt.where(VendorLocation.vendor_id == vendor_id)
    if point is not None:
        distance = func.round(
            cast(func.ST_Distance(Place.geom, point_expr(*point)), Numeric), 1
        ).label("distance_m")
        stmt = stmt.add_columns(distance).order_by(distance, VendorLocation.name)
    else:
        stmt = stmt.order_by(VendorLocation.name)
    result = await db.execute(stmt)
    rows: list[LocationRow] = []
    at = to_household(open_at) if open_at is not None else None
    for row in result.unique():
        location = row[0]
        distance_m = row[1] if point is not None else None
        is_open = is_open_at(location, at) if at is not None else None
        if at is not None and not is_open:
            continue
        rows.append(LocationRow(location, distance_m, is_open))
    return rows


async def get_location(db: AsyncSession, location_id: uuid.UUID) -> VendorLocation:
    return await _load_location(db, location_id)


async def list_stalls(db: AsyncSession, location_id: uuid.UUID) -> list[VendorLocation]:
    result = await db.execute(
        _location_query()
        .where(VendorLocation.parent_location_id == location_id)
        .order_by(VendorLocation.name)
    )
    return list(result.unique().scalars())


async def update_location(
    db: AsyncSession, location_id: uuid.UUID, changes: dict[str, Any]
) -> VendorLocation:
    location = await _load_location(db, location_id)
    if "name" in changes:
        location.name = _clean(changes["name"], required=True) or ""
    if "address" in changes:
        location.address = _clean(changes["address"])
    if "opening_hours" in changes:
        location.opening_hours = _hours(changes["opening_hours"])
    if "stop_overhead_min" in changes:
        location.stop_overhead_min = changes["stop_overhead_min"]
    if "receipt_identifiers" in changes:
        location.receipt_identifiers = [
            s.strip() for s in changes["receipt_identifiers"] or [] if s.strip()
        ]
    if "parent_location_id" in changes:
        parent_id = changes["parent_location_id"]
        if parent_id == location.id:
            raise ApiError(422, "validation_error", "A location cannot be its own parent.")
        if parent_id is not None and await _has_stalls(db, location.id):
            raise ApiError(
                422, "has_stalls", "This location has stalls of its own and cannot become a stall."
            )
        location.parent = await _resolve_parent(db, parent_id)
    if "lat" in changes or "lon" in changes:
        _move_place(location.place, changes.get("lat"), changes.get("lon"))
    if "home_base_id" in changes:
        location.home_base_id = await _resolve_home_base(
            db, changes, location.place.lat, location.place.lon
        )
    await _commit(db)
    return await _load_location(db, location_id)


async def set_location_active(
    db: AsyncSession, location_id: uuid.UUID, active: bool
) -> VendorLocation:
    location = await _load_location(db, location_id)
    location.active = active
    await _commit(db)
    return await _load_location(db, location_id)


# --- OpenStreetMap adoption -----------------------------------------------------


@dataclass
class CandidateRow:
    candidate: osm.OsmCandidate
    already_adopted: bool


async def _adopted_keys(db: AsyncSession) -> set[tuple[str, int]]:
    result = await db.execute(
        select(VendorLocation.osm_type, VendorLocation.osm_id).where(
            VendorLocation.osm_id.is_not(None)
        )
    )
    return {(t, i) for t, i in result.all()}


async def osm_candidates(
    db: AsyncSession, *, home_base_id: uuid.UUID, radius_m: int
) -> list[CandidateRow]:
    osm.ensure_enabled()
    home_base = await _load_home_base(db, home_base_id)
    items = await osm.candidates_around(
        home_base.id, home_base.place.lat, home_base.place.lon, radius_m
    )
    adopted = await _adopted_keys(db)
    return [CandidateRow(c, (c.osm_type, c.osm_id) in adopted) for c in items]


async def adopt_osm(
    db: AsyncSession,
    *,
    osm_type: str,
    osm_id: int,
    home_base_id: uuid.UUID,
    radius_m: int,
    vendor_kind: str | None,
) -> VendorLocation:
    """Vendor (if needed), place, and location in one transaction."""
    osm.ensure_enabled()
    home_base = await _load_home_base(db, home_base_id)
    existing = await db.scalar(
        select(VendorLocation.id).where(
            VendorLocation.osm_type == osm_type, VendorLocation.osm_id == osm_id
        )
    )
    if existing is not None:
        raise ApiError(
            409,
            "already_adopted",
            "That OpenStreetMap object is already adopted.",
            {"location_id": str(existing)},
        )
    items = await osm.candidates_around(
        home_base.id, home_base.place.lat, home_base.place.lon, radius_m
    )
    candidate = next((c for c in items if (c.osm_type, c.osm_id) == (osm_type, osm_id)), None)
    if candidate is None:
        raise ApiError(
            404,
            "osm_candidate_not_found",
            "That object is not among the candidates for this home base and radius.",
        )
    if candidate.name is None:
        raise ApiError(
            422, "osm_candidate_unnamed", "That object has no name in OpenStreetMap; add it by pin."
        )
    vendor = await find_vendor_by_name(db, candidate.name)
    if vendor is None:
        vendor = Vendor(
            name=candidate.name,
            kind=vendor_kind or candidate.kind_guess,
            price_scope="location",
            active=True,
        )
        db.add(vendor)
    location = VendorLocation(
        vendor=vendor,
        place=_new_place(candidate.lat, candidate.lon, candidate.name),
        home_base_id=home_base.id,
        name=candidate.name,
        address=candidate.address,
        osm_type=candidate.osm_type,
        osm_id=candidate.osm_id,
        osm_name=candidate.name,
        osm_address=candidate.address,
        osm_opening_hours=candidate.opening_hours,
        opening_hours=candidate.opening_hours,
        receipt_identifiers=[],
        active=True,
    )
    db.add(location)
    await _commit(db)
    return await _load_location(db, location.id)


async def refresh_osm(db: AsyncSession, location_id: uuid.UUID) -> VendorLocation:
    """Re-fetch tags. A field is overwritten only while it still equals its OSM snapshot."""
    osm.ensure_enabled()
    location = await _load_location(db, location_id)
    if location.osm_type is None or location.osm_id is None:
        raise ApiError(409, "not_adopted", "This location was not adopted from OpenStreetMap.")
    fresh = await osm.fetch_by_id(location.osm_type, location.osm_id)
    if fresh is None:
        raise ApiError(404, "osm_object_not_found", "That object no longer exists in OSM.")
    for field, snapshot_field, new_value in (
        ("name", "osm_name", fresh.name),
        ("address", "osm_address", fresh.address),
        ("opening_hours", "osm_opening_hours", fresh.opening_hours),
    ):
        user_edited = getattr(location, field) != getattr(location, snapshot_field)
        if not user_edited and not (field == "name" and new_value is None):
            setattr(location, field, new_value)
        setattr(location, snapshot_field, new_value)
    await _commit(db)
    return await _load_location(db, location_id)
