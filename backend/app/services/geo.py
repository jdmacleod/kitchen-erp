"""Home bases, vendors, vendor locations, and OSM adoption. Routers stay thin; rules live here.

Conventions: vendors and locations are deactivated, never deleted, because a
purchase may reference them. Home bases may be deleted while nothing points at
them. A new location's home base is the nearest one by geodesic distance unless
the caller names one or explicitly clears it. A stall (a location with a parent)
inherits its parent's opening hours while its own are null; a parent must itself
be a top-level location, so stalls do not nest.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Numeric, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.core.errors import ApiError
from app.models.geo import HomeBase, Place, Vendor, VendorLocation, point_expr
from app.models.purchases import Purchase
from app.services import osm
from app.services import phone as phones
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


def _phone(value: str | None) -> str | None:
    cleaned = _clean(value)
    if cleaned is not None and (error := phones.phone_error(cleaned)) is not None:
        raise ApiError(422, "invalid_phone", error)
    return cleaned


# --- provenance ----------------------------------------------------------------

# Location fields whose last OpenStreetMap value also lives in an ``osm_*`` column.
# Locations adopted before ``field_source`` existed have only these.
OSM_SNAPSHOTS = {"name": "osm_name", "address": "osm_address", "opening_hours": "osm_opening_hours"}


def _last_written(obj: Vendor | VendorLocation, name: str) -> Any:
    record = (obj.field_source or {}).get(name)
    if isinstance(record, dict) and "imported" in record:
        return record["imported"]
    snapshot = OSM_SNAPSHOTS.get(name) if isinstance(obj, VendorLocation) else None
    return getattr(obj, snapshot) if snapshot else None


def edit_outcome(current: Any, last_written: Any, value: Any) -> str:
    """What writing ``value`` over ``current`` would do.

    ``unchanged`` when they are equal; ``kept`` when a person changed the field
    since a source last wrote it (it no longer equals ``last_written``), so their
    edit wins; otherwise ``filled`` (it was empty) or ``updated``.
    """
    if value == current:
        return "unchanged"
    if current != last_written:
        return "kept"
    return "filled" if current is None else "updated"


def write_unless_edited(
    obj: Vendor | VendorLocation,
    name: str,
    value: Any,
    *,
    source: str,
    ref: str | None,
    now: datetime,
    only_if_empty: bool = False,
) -> str:
    """Write ``value`` to ``obj.<name>`` unless a person has edited the field.

    The one rule for every writer that is not a person (OSM link and refresh, and
    later import and accepted suggestions). ``field_source[name].imported``
    always advances to what the source now says, like the ``osm_*`` snapshots, so
    a field is a person's exactly while it differs from its source. With
    ``only_if_empty`` a field that holds any value is left entirely alone.
    Returns the ``edit_outcome``.
    """
    current = getattr(obj, name)
    if only_if_empty and current is not None:
        return "unchanged" if value == current else "kept"
    outcome = edit_outcome(current, _last_written(obj, name), value)
    if outcome in ("filled", "updated"):
        setattr(obj, name, value)
    record = {"source": source, "ref": ref, "checked_at": now.isoformat(), "imported": value}
    obj.field_source = {**(obj.field_source or {}), name: record}
    return outcome


def sources_of(obj: Vendor | VendorLocation, names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Where each field's current value came from, for the fields a source still owns.

    A field a person has changed since its source wrote it is absent: it was
    entered by hand.
    """
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        current = getattr(obj, name)
        if current is None:
            continue
        record = (obj.field_source or {}).get(name)
        if isinstance(record, dict) and "imported" in record:
            if record["imported"] == current:
                out[name] = {k: record.get(k) for k in ("source", "ref", "checked_at")}
        elif (
            isinstance(obj, VendorLocation)
            and obj.osm_type is not None
            and name in OSM_SNAPSHOTS
            and getattr(obj, OSM_SNAPSHOTS[name]) == current
        ):
            out[name] = {"source": "osm", "ref": f"{obj.osm_type}/{obj.osm_id}", "checked_at": None}
    return out


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


@dataclass(frozen=True)
class VendorSummary:
    """A vendor with what its card shows: active locations and the last committed visit."""

    vendor: Vendor
    location_count: int
    last_visit: datetime | None


async def list_vendors(
    db: AsyncSession,
    *,
    q: str | None = None,
    include_inactive: bool = False,
    limit: int | None = None,
) -> list[VendorSummary]:
    location_count = (
        select(func.count())
        .where(VendorLocation.vendor_id == Vendor.id, VendorLocation.active.is_(True))
        .correlate(Vendor)
        .scalar_subquery()
    )
    # Committed only: a draft or reopened purchase is not a visit yet.
    last_visit = (
        select(func.max(Purchase.purchased_at))
        .join(VendorLocation, VendorLocation.id == Purchase.vendor_location_id)
        .where(VendorLocation.vendor_id == Vendor.id, Purchase.status == "committed")
        .correlate(Vendor)
        .scalar_subquery()
    )
    stmt = select(Vendor, location_count, last_visit)
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
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [VendorSummary(v, count, visit) for v, count, visit in result.all()]


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
        phone=_phone(data.get("phone")),
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
        # Filtering by "open at" removes only locations known to be closed; a
        # location with no recorded hours is unknown, not closed, and stays.
        if at is not None and is_open is False:
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
    if "phone" in changes:
        location.phone = _phone(changes["phone"])
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
        osm_type=candidate.osm_type,
        osm_id=candidate.osm_id,
        receipt_identifiers=[],
        field_source={},
        active=True,
    )
    _apply_osm(location, vendor, candidate, datetime.now(UTC))
    db.add(location)
    await _commit(db)
    return await _load_location(db, location.id)


async def refresh_osm(db: AsyncSession, location_id: uuid.UUID) -> VendorLocation:
    """Re-fetch tags. A field is overwritten only while it still equals its OSM snapshot."""
    osm.ensure_enabled()
    location = await _load_location(db, location_id)
    if location.osm_type is None or location.osm_id is None:
        raise ApiError(409, "not_adopted", "This location is not linked to OpenStreetMap.")
    fresh = await osm.fetch_by_id(location.osm_type, location.osm_id)
    if fresh is None:
        raise ApiError(404, "osm_object_not_found", "That object no longer exists in OSM.")
    _apply_osm(location, location.vendor, fresh, datetime.now(UTC))
    await _commit(db)
    return await _load_location(db, location_id)


# Location fields OSM writes, in order.
_OSM_LOCATION_FIELDS = ("name", "address", "opening_hours", "phone")


def _osm_values(candidate: osm.OsmCandidate) -> dict[str, Any]:
    return {name: getattr(candidate, name) for name in _OSM_LOCATION_FIELDS}


def _apply_osm(
    location: VendorLocation, vendor: Vendor, candidate: osm.OsmCandidate, now: datetime
) -> dict[str, str]:
    """Write a candidate's facts where no person has edited them; advance the snapshots.

    A name missing from OSM never blanks a location. The vendor's website is
    filled only while the vendor has none. Returns each field's ``edit_outcome``.
    """
    ref = f"{candidate.osm_type}/{candidate.osm_id}"
    outcomes: dict[str, str] = {}
    for name, value in _osm_values(candidate).items():
        if not (name == "name" and value is None):
            outcomes[name] = write_unless_edited(
                location, name, value, source="osm", ref=ref, now=now
            )
        if name in OSM_SNAPSHOTS:
            setattr(location, OSM_SNAPSHOTS[name], value)
    if candidate.website is not None:
        outcomes["website"] = write_unless_edited(
            vendor, "website", candidate.website, source="osm", ref=ref, now=now, only_if_empty=True
        )
    return outcomes


def osm_preview(
    location: VendorLocation, candidate: osm.OsmCandidate
) -> tuple[list[str], list[str]]:
    """Which fields linking ``candidate`` would fill or update, and which a person's edit keeps."""
    fills: list[str] = []
    keeps: list[str] = []
    for name, value in _osm_values(candidate).items():
        if value is None:
            continue
        outcome = edit_outcome(getattr(location, name), _last_written(location, name), value)
        if outcome in ("filled", "updated"):
            fills.append(name)
        elif outcome == "kept":
            keeps.append(name)
    if candidate.website is not None and location.vendor.website is None:
        fills.append("website")
    return fills, keeps


def _distance_m(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> int:
    """Great-circle metres, for display only (rounded; never stored)."""
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp, dl = p2 - p1, math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * 6_371_008.8 * math.asin(math.sqrt(a)))


@dataclass
class LinkCandidate:
    candidate: osm.OsmCandidate
    distance_m: int
    linked_to: VendorLocation | None
    fills: list[str] = dc_field(default_factory=list)
    keeps: list[str] = dc_field(default_factory=list)


async def location_osm_candidates(
    db: AsyncSession, location_id: uuid.UUID, *, radius_m: int
) -> list[LinkCandidate]:
    """OSM objects near a location's pin, nearest first, each with what linking would do."""
    osm.ensure_enabled()
    location = await _load_location(db, location_id)
    items = await osm.candidates_around(
        f"location:{location.id}", location.place.lat, location.place.lon, radius_m
    )
    linked = await db.execute(select(VendorLocation).where(VendorLocation.osm_id.is_not(None)))
    by_key = {(loc.osm_type, loc.osm_id): loc for loc in linked.unique().scalars()}
    rows = []
    for c in items:
        fills, keeps = osm_preview(location, c)
        rows.append(
            LinkCandidate(
                candidate=c,
                distance_m=_distance_m(location.place.lat, location.place.lon, c.lat, c.lon),
                linked_to=by_key.get((c.osm_type, c.osm_id)),
                fills=fills,
                keeps=keeps,
            )
        )
    rows.sort(key=lambda r: (r.distance_m, r.candidate.name or ""))
    return rows


async def link_osm(
    db: AsyncSession, location_id: uuid.UUID, *, osm_type: str, osm_id: int, radius_m: int
) -> VendorLocation:
    """Link an existing location to an OSM object near its pin, and take its facts (1F)."""
    osm.ensure_enabled()
    location = await _load_location(db, location_id)
    if location.osm_type is not None:
        raise ApiError(409, "already_linked", "This location is already linked; unlink it first.")
    other = await db.scalar(
        select(VendorLocation.id).where(
            VendorLocation.osm_type == osm_type, VendorLocation.osm_id == osm_id
        )
    )
    if other is not None:
        raise ApiError(
            409,
            "already_adopted",
            "That OpenStreetMap object is already linked to another location.",
            {"location_id": str(other)},
        )
    items = await osm.candidates_around(
        f"location:{location.id}", location.place.lat, location.place.lon, radius_m
    )
    candidate = next((c for c in items if (c.osm_type, c.osm_id) == (osm_type, osm_id)), None)
    if candidate is None:
        raise ApiError(
            404,
            "osm_candidate_not_found",
            "That object is not among the OpenStreetMap places near this location.",
        )
    location.osm_type, location.osm_id = candidate.osm_type, candidate.osm_id
    _apply_osm(location, location.vendor, candidate, datetime.now(UTC))
    await _commit(db)
    return await _load_location(db, location_id)


async def unlink_osm(db: AsyncSession, location_id: uuid.UUID) -> VendorLocation:
    """Forget the OSM link. The location keeps every value it has."""
    location = await _load_location(db, location_id)
    if location.osm_type is None:
        raise ApiError(409, "not_adopted", "This location is not linked to OpenStreetMap.")
    location.osm_type, location.osm_id = None, None
    await _commit(db)
    return await _load_location(db, location_id)


async def linked_location_ids(db: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    """Active locations linked to OSM, for ``kerp osm refresh --all-linked``."""
    result = await db.execute(
        select(VendorLocation.id, VendorLocation.name)
        .where(VendorLocation.osm_id.is_not(None), VendorLocation.active.is_(True))
        .order_by(VendorLocation.name)
    )
    return [(i, n) for i, n in result.all()]
