"""Home bases, vendors, vendor locations, and OSM adoption (Phase 1D)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, Idempotency
from app.schemas.geo import (
    HomeBaseCreate,
    HomeBaseList,
    HomeBaseOut,
    HomeBaseUpdate,
    IsOpenOut,
    OpeningHoursValidateIn,
    OpeningHoursValidateOut,
    OsmAdoptIn,
    OsmCandidateList,
    OsmCandidateOut,
    VendorCreate,
    VendorList,
    VendorListItem,
    VendorLocationCreate,
    VendorLocationDetail,
    VendorLocationList,
    VendorLocationOut,
    VendorLocationUpdate,
    VendorOut,
    VendorUpdate,
)
from app.services import geo
from app.services.opening_hours import is_open_at, to_household, validate_hours

router = APIRouter(tags=["geo"])

home_bases = APIRouter(prefix="/home-bases", tags=["home-bases"])
vendors = APIRouter(prefix="/vendors", tags=["vendors"])
locations = APIRouter(prefix="/vendor-locations", tags=["vendor-locations"])
hours = APIRouter(prefix="/opening-hours", tags=["opening-hours"])
osm = APIRouter(prefix="/osm", tags=["osm"])


async def _detail(db: DbSession, location_id: uuid.UUID) -> VendorLocationDetail:
    location = await geo.get_location(db, location_id)
    stalls = await geo.list_stalls(db, location_id)
    return VendorLocationDetail.from_model_with_stalls(location, stalls)


# --- home bases --------------------------------------------------------------


@home_bases.get("", response_model=HomeBaseList)
async def list_home_bases(_: CurrentUser, db: DbSession) -> HomeBaseList:
    return HomeBaseList(items=[HomeBaseOut.from_model(h) for h in await geo.list_home_bases(db)])


@home_bases.post("", response_model=HomeBaseOut, status_code=status.HTTP_201_CREATED)
async def create_home_base(
    payload: HomeBaseCreate, _: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    home_base = await geo.create_home_base(
        db, name=payload.name, lat=payload.lat, lon=payload.lon, label=payload.label
    )
    return await guard.commit(201, HomeBaseOut.from_model(home_base).model_dump(mode="json"))


@home_bases.get("/{home_base_id}", response_model=HomeBaseOut)
async def get_home_base(home_base_id: uuid.UUID, _: CurrentUser, db: DbSession) -> HomeBaseOut:
    return HomeBaseOut.from_model(await geo.get_home_base(db, home_base_id))


@home_bases.patch("/{home_base_id}", response_model=HomeBaseOut)
async def update_home_base(
    home_base_id: uuid.UUID, payload: HomeBaseUpdate, _: CurrentUser, db: DbSession
) -> HomeBaseOut:
    changes = payload.model_dump(exclude_unset=True)
    return HomeBaseOut.from_model(await geo.update_home_base(db, home_base_id, changes))


@home_bases.delete("/{home_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_home_base(home_base_id: uuid.UUID, _: CurrentUser, db: DbSession) -> Response:
    await geo.delete_home_base(db, home_base_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- vendors -----------------------------------------------------------------


@vendors.get("", response_model=VendorList)
async def list_vendors(
    _: CurrentUser,
    db: DbSession,
    q: str | None = Query(default=None, max_length=200),
    include_inactive: bool = False,
) -> VendorList:
    found = await geo.list_vendors(db, q=q, include_inactive=include_inactive)
    return VendorList(
        items=[
            VendorListItem(
                **VendorOut.model_validate(f.vendor).model_dump(),
                location_count=f.location_count,
                last_visit=f.last_visit,
            )
            for f in found
        ]
    )


@vendors.post("", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    payload: VendorCreate, _: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    vendor = await geo.create_vendor(
        db,
        name=payload.name,
        kind=payload.kind,
        price_scope=payload.price_scope,
        website=payload.website,
        notes=payload.notes,
    )
    return await guard.commit(201, VendorOut.model_validate(vendor).model_dump(mode="json"))


@vendors.get("/{vendor_id}", response_model=VendorOut)
async def get_vendor(vendor_id: uuid.UUID, _: CurrentUser, db: DbSession) -> VendorOut:
    return VendorOut.model_validate(await geo.get_vendor(db, vendor_id))


@vendors.patch("/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: uuid.UUID, payload: VendorUpdate, _: CurrentUser, db: DbSession
) -> VendorOut:
    changes = payload.model_dump(exclude_unset=True)
    return VendorOut.model_validate(await geo.update_vendor(db, vendor_id, changes))


@vendors.post("/{vendor_id}/deactivate", response_model=VendorOut)
async def deactivate_vendor(vendor_id: uuid.UUID, _: CurrentUser, db: DbSession) -> VendorOut:
    return VendorOut.model_validate(await geo.set_vendor_active(db, vendor_id, False))


@vendors.post("/{vendor_id}/activate", response_model=VendorOut)
async def activate_vendor(vendor_id: uuid.UUID, _: CurrentUser, db: DbSession) -> VendorOut:
    return VendorOut.model_validate(await geo.set_vendor_active(db, vendor_id, True))


# --- vendor locations ----------------------------------------------------------


@locations.get("", response_model=VendorLocationList)
async def list_locations(
    _: CurrentUser,
    db: DbSession,
    near: str | None = Query(default=None, max_length=64),
    kind: Literal["chain", "independent", "market", "stand"] | None = None,
    home_base_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    open_at: datetime | None = None,
    include_inactive: bool = False,
) -> VendorLocationList:
    rows = await geo.list_locations(
        db,
        near=near,
        kind=kind,
        home_base_id=home_base_id,
        vendor_id=vendor_id,
        open_at=open_at,
        include_inactive=include_inactive,
    )
    return VendorLocationList(
        items=[
            VendorLocationOut.from_model(r.location, is_open=r.is_open, distance_m=r.distance_m)
            for r in rows
        ]
    )


@locations.post("", response_model=VendorLocationDetail, status_code=status.HTTP_201_CREATED)
async def create_location(
    payload: VendorLocationCreate, _: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    location = await geo.create_location(db, payload.model_dump(exclude_unset=True))
    body = await _detail(db, location.id)
    return await guard.commit(201, body.model_dump(mode="json"))


@locations.get("/{location_id}", response_model=VendorLocationDetail)
async def get_location(
    location_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> VendorLocationDetail:
    return await _detail(db, location_id)


@locations.patch("/{location_id}", response_model=VendorLocationDetail)
async def update_location(
    location_id: uuid.UUID, payload: VendorLocationUpdate, _: CurrentUser, db: DbSession
) -> VendorLocationDetail:
    await geo.update_location(db, location_id, payload.model_dump(exclude_unset=True))
    return await _detail(db, location_id)


@locations.post("/{location_id}/deactivate", response_model=VendorLocationDetail)
async def deactivate_location(
    location_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> VendorLocationDetail:
    await geo.set_location_active(db, location_id, False)
    return await _detail(db, location_id)


@locations.post("/{location_id}/activate", response_model=VendorLocationDetail)
async def activate_location(
    location_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> VendorLocationDetail:
    await geo.set_location_active(db, location_id, True)
    return await _detail(db, location_id)


@locations.get("/{location_id}/is-open", response_model=IsOpenOut)
async def location_is_open(
    location_id: uuid.UUID, _: CurrentUser, db: DbSession, at: datetime | None = None
) -> IsOpenOut:
    location = await geo.get_location(db, location_id)
    instant = to_household(at if at is not None else datetime.now(UTC))
    return IsOpenOut(
        id=location.id,
        at=instant.astimezone(UTC),
        is_open=is_open_at(location, instant),
        effective_opening_hours=VendorLocationOut.from_model(location).effective_opening_hours,
    )


@locations.post("/{location_id}/refresh-osm", response_model=VendorLocationDetail)
async def refresh_location_osm(
    location_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> VendorLocationDetail:
    await geo.refresh_osm(db, location_id)
    return await _detail(db, location_id)


# --- opening hours ---------------------------------------------------------------


@hours.post("/validate", response_model=OpeningHoursValidateOut)
async def validate_opening_hours(
    payload: OpeningHoursValidateIn, _: CurrentUser
) -> OpeningHoursValidateOut:
    error = validate_hours(payload.text)
    return OpeningHoursValidateOut(valid=error is None, error=error)


# --- OpenStreetMap ---------------------------------------------------------------


@osm.get("/candidates", response_model=OsmCandidateList)
async def osm_candidates(
    _: CurrentUser,
    db: DbSession,
    home_base_id: uuid.UUID,
    radius_m: int = Query(ge=100, le=50000),
) -> OsmCandidateList:
    rows = await geo.osm_candidates(db, home_base_id=home_base_id, radius_m=radius_m)
    return OsmCandidateList(
        items=[
            OsmCandidateOut(
                osm_type=r.candidate.osm_type,  # type: ignore[arg-type]
                osm_id=r.candidate.osm_id,
                name=r.candidate.name,
                kind_guess=r.candidate.kind_guess,  # type: ignore[arg-type]
                lat=r.candidate.lat,
                lon=r.candidate.lon,
                address=r.candidate.address,
                opening_hours=r.candidate.opening_hours,
                already_adopted=r.already_adopted,
            )
            for r in rows
        ]
    )


@osm.post("/adopt", response_model=VendorLocationDetail, status_code=status.HTTP_201_CREATED)
async def osm_adopt(
    payload: OsmAdoptIn, _: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    location = await geo.adopt_osm(
        db,
        osm_type=payload.osm_type,
        osm_id=payload.osm_id,
        home_base_id=payload.home_base_id,
        radius_m=payload.radius_m,
        vendor_kind=payload.vendor_kind,
    )
    body = await _detail(db, location.id)
    return await guard.commit(201, body.model_dump(mode="json"))


router.include_router(home_bases)
router.include_router(vendors)
router.include_router(locations)
router.include_router(hours)
router.include_router(osm)
