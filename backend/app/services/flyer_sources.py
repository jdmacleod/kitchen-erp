"""A vendor location's circular (18, 2U-4; UI-9.5): what the helper reads, its last
runs, and "Check now".

The same fields ``kerp flyers import`` seeds, set by a person. Store ids stay in
the household's database; this repository never names one (18, Privacy).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import new_id
from app.models import AdSource, FlyerRun, LookupRequest
from app.models.geo import VendorLocation
from app.services import flyers
from app.services.lookups import helper_configured

# The card shows this many of the newest runs.
RUNS_SHOWN = 4
# Sources a model reads ask before their offers are used, until turned off (2W).
_ASKS_FIRST = ("image", "region")


async def _location(db: AsyncSession, location_id: uuid.UUID) -> VendorLocation:
    location = await db.get(VendorLocation, location_id)
    if location is None:
        raise ApiError(404, "not_found", "No such vendor location.")
    return location


async def _source(db: AsyncSession, location_id: uuid.UUID) -> AdSource | None:
    return (
        await db.execute(select(AdSource).where(AdSource.vendor_location_id == location_id))
    ).scalar_one_or_none()


async def circular(db: AsyncSession, location_id: uuid.UUID) -> dict[str, Any]:
    """The location's source, if it has one, its newest runs, and whether a read is
    waiting for the helper."""
    await _location(db, location_id)
    source = await _source(db, location_id)
    if source is None:
        return {"source": None, "runs": [], "checking": False}
    runs = (
        await db.execute(
            select(FlyerRun)
            .where(FlyerRun.ad_source_id == source.id)
            .order_by(FlyerRun.created_at.desc(), FlyerRun.id.desc())
            .limit(RUNS_SHOWN)
        )
    ).scalars()
    checking = (
        await db.execute(
            select(LookupRequest.id).where(
                LookupRequest.ad_source_id == source.id, LookupRequest.status == "open"
            )
        )
    ).first() is not None
    return {"source": source, "runs": list(runs), "checking": checking}


async def put_circular(
    db: AsyncSession, location_id: uuid.UUID, values: dict[str, Any]
) -> dict[str, Any]:
    """Set up the location's circular, or replace what the helper is told."""
    await _location(db, location_id)
    if values.get("require_confirmation") is None:
        values["require_confirmation"] = values["mechanism"] in _ASKS_FIRST
    source = await _source(db, location_id)
    if source is None:
        db.add(AdSource(id=new_id(), vendor_location_id=location_id, **values))
    else:
        for key, value in values.items():
            setattr(source, key, value)
    await db.commit()
    return await circular(db, location_id)


async def patch_circular(
    db: AsyncSession, location_id: uuid.UUID, values: dict[str, Any]
) -> dict[str, Any]:
    """Turn the circular on or off, or change whether it asks first or its weekday."""
    await _location(db, location_id)
    source = await _source(db, location_id)
    if source is None:
        raise ApiError(404, "not_found", "This location has no circular set up.")
    for key, value in values.items():
        setattr(source, key, value)
    await db.commit()
    return await circular(db, location_id)


async def check_now(
    db: AsyncSession, location_id: uuid.UUID, now: datetime | None = None
) -> dict[str, Any]:
    """Ask the helper to read this ad week now, whatever the week's tries so far.

    A person asking lifts a pause. A read of a week already read makes a new run,
    which the read models then use (U5's rule)."""
    now = now or datetime.now(UTC)
    await _location(db, location_id)
    source = await _source(db, location_id)
    if source is None:
        raise ApiError(404, "not_found", "This location has no circular set up.")
    if not source.enabled:
        raise ApiError(409, "source_off", "Turn the circular on first.")
    if not await helper_configured(db):
        raise ApiError(409, "no_helper", "No products helper is set up to read it.")
    if (await circular(db, location_id))["checking"]:
        raise ApiError(409, "already_checking", "A read of it is already waiting.")
    source.paused_until = None
    week = flyers.ad_week_start(source.publish_weekday, flyers.today(now))
    db.add(
        LookupRequest(
            id=new_id(),
            kind="flyer",
            ad_source_id=source.id,
            value=week.isoformat(),
            created_at=now,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        # Someone else, or the weekly queue, asked for a read a moment earlier: the
        # index allows one open request per source.
        await db.rollback()
        if "uq_lookup_request_open_ad_source" not in str(exc.orig):
            raise
        raise ApiError(409, "already_checking", "A read of it is already waiting.") from exc
    return await circular(db, location_id)
