"""Weekly flyers (18, 2U): the sources, the weekly queue, and the helper's reports.

```
ad_source (enabled) ─▶ worker, hourly when idle: due this ad week? ─▶ lookup_request (flyer)
helper reads the queue ─▶ fetches, archives, normalizes ─▶ POST /flyer-reports
report ─▶ validate (kitchen-erp-products/1) ─▶ refused? recorded, 422, request stays open
       ─▶ flyer_run ─▶ relevance ─▶ flyer_offer per relevant candidate; the rest counted
```

This application makes none of the helper's calls (non-negotiable 9), and no
offer is ever a price observation (18, F4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.ids import new_id
from app.models import (
    AdSource,
    FlyerOffer,
    FlyerRun,
    LookupRequest,
    UnitRow,
)
from app.models.geo import VendorLocation
from app.schemas.lookups import FlyerSourceOut, LookupRequestOut
from app.schemas.products_interchange import FlyerReport
from app.services import flyer_relevance, lookups
from app.services.lookups import _record, _refused, helper_configured

# A source gets this many attempts at an ad week, at least a day apart.
ATTEMPTS_PER_WEEK = 3
RETRY_AFTER = timedelta(days=1)
# Two ad weeks in a row that end failed pause a source: 7 days, doubling to 56.
PAUSE_AFTER_WEEKS = 2
FIRST_PAUSE_DAYS = 7
LONGEST_PAUSE_DAYS = 56


def today(now: datetime | None = None) -> date:
    """The household's date."""
    zone = ZoneInfo(get_settings().household_timezone)
    return (now or datetime.now(UTC)).astimezone(zone).date()


def ad_week_start(publish_weekday: int, on: date) -> date:
    """The most recent day, on or before ``on``, that is the source's publication day."""
    return on - timedelta(days=(on.isoweekday() - publish_weekday) % 7)


# --- the registry ---------------------------------------------------------------------


class SourceSeed(BaseModel):
    """One line of a household's seed file (``kerp flyers import``)."""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=3, max_length=250)  # the vendor location's key
    mechanism: Literal["hosted_circular", "storefront", "itemized", "image", "region"]
    adapter: str = Field(min_length=1, max_length=64)
    store_ref: dict[str, str] = {}
    publish_weekday: int = Field(ge=1, le=7)
    enabled: bool = False
    require_confirmation: bool | None = None
    tiles: dict[str, Any] | None = None


class SeedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[SourceSeed]


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    unmatched: list[str] = field(default_factory=list)


_SEEDED = (
    "mechanism",
    "adapter",
    "store_ref",
    "publish_weekday",
    "enabled",
    "require_confirmation",
    "tiles",
)


async def import_sources(db: AsyncSession, seed: SeedFile) -> ImportReport:
    """Create or update one source per seeded location, matched by location key.

    Importing the same file twice changes nothing the second time. A key that
    names no vendor location is reported and skipped.
    """
    report = ImportReport()
    for line in seed.sources:
        location = (
            await db.execute(select(VendorLocation).where(VendorLocation.key == line.location))
        ).scalar_one_or_none()
        if location is None:
            report.unmatched.append(line.location)
            continue
        values = line.model_dump()
        if values["require_confirmation"] is None:
            values["require_confirmation"] = line.mechanism in ("image", "region")
        source = (
            await db.execute(select(AdSource).where(AdSource.vendor_location_id == location.id))
        ).scalar_one_or_none()
        if source is None:
            db.add(
                AdSource(
                    id=new_id(),
                    vendor_location_id=location.id,
                    **{k: values[k] for k in _SEEDED},
                )
            )
            report.created += 1
            continue
        changed = [k for k in _SEEDED if getattr(source, k) != values[k]]
        for key in changed:
            setattr(source, key, values[key])
        if changed:
            report.updated += 1
        else:
            report.unchanged += 1
    await db.commit()
    return report


# --- the weekly queue -----------------------------------------------------------------


async def reading_receipts(db: AsyncSession) -> bool:
    """Whether a receipt is being read or read again: flyer work waits for it (F11)."""
    return bool(
        (
            await db.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM ingest_job "
                    "WHERE status IN ('pending', 'running')) OR EXISTS (SELECT 1 FROM line_reread "
                    "WHERE status IN ('pending', 'running'))"
                )
            )
        ).scalar()
    )


_DUE = """
    SELECT s.id, s.publish_weekday FROM ad_source s
    JOIN vendor_location vl ON vl.id = s.vendor_location_id
    WHERE s.enabled AND vl.active
      AND (s.paused_until IS NULL OR s.paused_until <= CAST(:now AS timestamptz))
      AND NOT EXISTS (
          SELECT 1 FROM lookup_request r WHERE r.ad_source_id = s.id AND r.status = 'open'
      )
"""

_WEEK = """
    SELECT count(DISTINCT r.id) AS attempts,
           max(r.created_at) AS last_at,
           bool_or(fr.status IN ('ok', 'empty', 'not_allowed')) AS settled
    FROM lookup_request r
    LEFT JOIN flyer_run fr ON fr.lookup_request_id = r.id
    WHERE r.ad_source_id = :source AND r.kind = 'flyer' AND r.value = :week
"""


async def queue_flyer_runs(db: AsyncSession, now: datetime | None = None) -> int:
    """Queue one ``flyer`` request for each source due a read of this ad week.

    Only while a products helper token exists and no receipt is being read. A
    week is settled by a run that read it (``ok``, ``empty``) or was refused by
    the site (``not_allowed``); otherwise it gets ``ATTEMPTS_PER_WEEK`` attempts,
    ``RETRY_AFTER`` apart. Returns how many were queued.
    """
    now = now or datetime.now(UTC)
    if not await helper_configured(db) or await reading_receipts(db):
        return 0
    on = today(now)
    queued = 0
    for source_id, weekday in (await db.execute(text(_DUE), {"now": now})).all():
        week = ad_week_start(weekday, on).isoformat()
        attempts, last_at, settled = (
            await db.execute(text(_WEEK), {"source": source_id, "week": week})
        ).one()
        if settled or attempts >= ATTEMPTS_PER_WEEK:
            continue
        if last_at is not None and now - last_at < RETRY_AFTER:
            continue
        db.add(
            LookupRequest(
                id=new_id(), kind="flyer", ad_source_id=source_id, value=week, created_at=now
            )
        )
        queued += 1
    await db.commit()
    return queued


# --- the helper's view of the queue -------------------------------------------------


async def queue_items(db: AsyncSession, now: datetime | None = None) -> list[LookupRequestOut]:
    """The open requests as the helper reads them, a flyer request with its source.

    A flyer request is held back while its source is turned off or paused, even
    if it was queued before that changed.
    """
    now = now or datetime.now(UTC)
    requests = await lookups.open_requests(db)
    source_ids = {r.ad_source_id for r in requests if r.ad_source_id}
    sources = {
        s.id: s
        for s in (await db.execute(select(AdSource).where(AdSource.id.in_(source_ids)))).scalars()
    }
    items = []
    for request in requests:
        item = LookupRequestOut.model_validate(request)
        if request.ad_source_id is not None:
            source = sources[request.ad_source_id]
            if not source.enabled or (source.paused_until and source.paused_until > now):
                continue
            item.flyer = FlyerSourceOut(
                mechanism=source.mechanism,  # type: ignore[arg-type]
                adapter=source.adapter,
                store_ref=source.store_ref,
                tiles=source.tiles,
                ad_week=date.fromisoformat(request.value or ""),
            )
        items.append(item)
    return items


# --- reports --------------------------------------------------------------------------


@dataclass
class Reported:
    run_id: uuid.UUID
    status: str
    offered: int
    relevant: int
    # Each candidate's offer, in order; None where it wasn't relevant.
    offer_ids: list[uuid.UUID | None]


async def _units(db: AsyncSession, codes: set[str]) -> set[str]:
    if not codes:
        return set()
    return set((await db.execute(select(UnitRow.code).where(UnitRow.code.in_(codes)))).scalars())


async def _weeks_failed(db: AsyncSession, request: LookupRequest, status: str) -> bool:
    """Whether this failed report ends its ad week: refused by the site, or the last try."""
    if status == "not_allowed":
        return True
    attempts = await db.scalar(
        select(func.count())
        .select_from(LookupRequest)
        .where(
            LookupRequest.ad_source_id == request.ad_source_id,
            LookupRequest.kind == "flyer",
            LookupRequest.value == request.value,
        )
    )
    return (attempts or 0) >= ATTEMPTS_PER_WEEK


async def _note_outcome(
    db: AsyncSession, source: AdSource, request: LookupRequest, status: str, now: datetime
) -> None:
    """Keep the source's pause in step: a read week clears it; failed weeks build it."""
    if status in ("ok", "empty"):
        source.failures = 0
        source.paused_until = None
        return
    if not await _weeks_failed(db, request, status):
        return
    source.failures += 1
    if source.failures >= PAUSE_AFTER_WEEKS:
        days = min(
            FIRST_PAUSE_DAYS * 2 ** (source.failures - PAUSE_AFTER_WEEKS), LONGEST_PAUSE_DAYS
        )
        source.paused_until = now + timedelta(days=days)


async def _locked_request(db: AsyncSession, request_id: uuid.UUID) -> LookupRequest | None:
    return (
        await db.execute(
            select(LookupRequest).where(LookupRequest.id == request_id).with_for_update()
        )
    ).scalar_one_or_none()


async def _refuse(db: AsyncSession, token_id: uuid.UUID | None, body: Any, detail: str) -> None:
    """Record a refused report; when it names an open flyer request, that attempt failed.

    The request is answered so the week's attempts and the source's pause count it,
    and the inbox shows the source as needing attention (18, U4). Nothing of the
    report itself is stored.
    """
    request = None
    raw_id = body.get("request_id") if isinstance(body, dict) else None
    try:
        request_id = uuid.UUID(str(raw_id)) if raw_id else None
    except ValueError:
        request_id = None
    if request_id is not None:
        request = await _locked_request(db, request_id)
    if request is not None and request.kind == "flyer" and request.status == "open":
        source = await db.get(AdSource, request.ad_source_id, with_for_update=True)
        now = datetime.now(UTC)
        request.status = "answered"
        request.answered_at = now
        if source is not None:
            await _note_outcome(db, source, request, "failed", now)
    else:
        request = None
    await _record(
        db,
        request.id if request else None,
        token_id,
        {"flyer_report": "refused"},
        "refused",
        detail,
    )


async def record_report(db: AsyncSession, token_id: uuid.UUID | None, body: Any) -> Reported:
    """Validate a helper's flyer report and keep its run and its relevant offers.

    A report may answer an open request, or report again on one already
    answered (a replay with a newer adapter); a closed request refuses it.
    """
    try:
        report = FlyerReport.model_validate(body)
    except ValidationError as exc:
        await _refuse(db, token_id, body, str(exc))
        raise _refused(str(exc)) from None
    request = await _locked_request(db, report.request_id)
    if request is None or request.kind != "flyer" or request.ad_source_id is None:
        raise ApiError(404, "not_found", "No such flyer request.")
    if request.status == "closed":
        raise ApiError(409, "request_closed", "That flyer request was closed.")
    source = await db.get(AdSource, request.ad_source_id, with_for_update=True)
    location = await db.get(VendorLocation, source.vendor_location_id) if source else None
    if source is None or location is None:
        raise ApiError(404, "not_found", "No such flyer source.")
    now = datetime.now(UTC)

    names = [flyer_relevance.normalize_name(c.name_as_printed) for c in report.candidates]
    why = await flyer_relevance.relevance(
        db, vendor_id=location.vendor_id, kitchen_id=location.home_base_id, names=names, now=now
    )
    units = await _units(db, {c.size_unit for c in report.candidates if c.size_unit})
    run = FlyerRun(
        id=new_id(),
        ad_source_id=source.id,
        lookup_request_id=request.id,
        status=report.status,
        reason=report.reason,
        adapter_version=report.adapter_version,
        ad_week_from=report.ad_week_from,
        ad_week_to=report.ad_week_to,
        fetched_at=report.fetched_at,
        offered_count=len(report.candidates),
        relevant_count=sum(1 for w in why if w),
    )
    db.add(run)
    await db.flush()
    offer_ids: list[uuid.UUID | None] = []
    for candidate, name, reason in zip(report.candidates, names, why, strict=True):
        if reason is None:
            offer_ids.append(None)
            continue
        # A size in a unit this system doesn't know is dropped, not the offer.
        sized = candidate.size_unit in units
        offer_ids.append(new_id())
        db.add(
            FlyerOffer(
                id=offer_ids[-1],
                flyer_run_id=run.id,
                name_norm=name,
                relevance=reason,
                **candidate.model_dump(exclude={"size_qty", "size_unit"}),
                size_qty=candidate.size_qty if sized else None,
                size_unit=candidate.size_unit if sized else None,
            )
        )
    # Only the first answer counts toward the source's pause; a replay of a week
    # already answered leaves it as it is.
    if request.status == "open":
        request.status = "answered"
        request.answered_at = now
        await _note_outcome(db, source, request, report.status, now)
    reported = Reported(run.id, run.status, run.offered_count, run.relevant_count, offer_ids)
    await _record(
        db,
        request.id,
        token_id,
        {"flyer_run": str(run.id), "status": run.status, "offered": run.offered_count},
        "merged",
    )
    return reported
