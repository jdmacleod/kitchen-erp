"""Vendor suggestions: intake from an outside tool, and a person's review (spec 03 §1F).

```
POST batch (≤ 200 items, ≤ 1 MB, pending ≤ 2,000) ─▶ keys resolve ─▶ each value
    checked as the edit form checks it ──any bad──▶ 422, nothing stored
  └─ok─▶ duplicates and proposals that already hold collapse ─▶ stored pending
pending ─accept─▶ field still as expected ? write + provenance : stale
stale ───accept(override)─▶ write + provenance       pending|stale ─reject─▶ rejected
```

Posting never changes a vendor or a location. Nothing is applied without a
person's click (non-negotiable 8). Proposals are untrusted text: validated
against the closed set of fields, never interpreted; evidence is plain text.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import AppUser
from app.models.geo import Vendor, VendorLocation
from app.models.suggestions import LOCATION_FIELDS, VENDOR_FIELDS, VendorSuggestion
from app.schemas.vendor_suggestions import (
    MAX_ITEMS,
    MAX_PENDING,
    SuggestionBatchIn,
    SuggestionIn,
    SuggestionOut,
    SuggestionSummary,
    SuggestionVendorCount,
)
from app.services import geo
from app.services import phone as phones

AWAITING = ("pending", "stale")
_WIKIDATA = re.compile(r"^Q[0-9]+$")


class _Invalid(Exception):
    pass


def _url(value: Any, *, limit: int = 500) -> str:
    if not isinstance(value, str):
        raise _Invalid("must be a web address")
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or len(value) > limit:
        raise _Invalid("must be an http or https address with a host")
    return value


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Invalid("must be text")
    value = value.strip()
    if len(value) > limit:
        raise _Invalid(f"must be at most {limit} characters")
    return value


def normalize(field: str, value: Any) -> Any:
    """A proposed or expected value as the edit form would store it, or _Invalid."""
    if field == "website":
        return _url(value)
    if field in ("brand", "name"):
        return _text(value, 200)
    if field == "address":
        return _text(value, 500)
    if field == "wikidata":
        if not isinstance(value, str) or not _WIKIDATA.match(value.strip()):
            raise _Invalid("must look like Q123")
        return value.strip()
    if field == "phone":
        text = _text(value, 40)
        if (error := phones.phone_error(text)) is not None:
            raise _Invalid(error)
        return text
    if field == "opening_hours":
        try:
            return geo._hours(_text(value, 2000))
        except ApiError as exc:
            raise _Invalid(exc.message) from None
    if field == "price_scope":
        if value not in ("chain", "location"):
            raise _Invalid("must be chain or location")
        return value
    if field == "osm":
        if (
            not isinstance(value, dict)
            or set(value) != {"type", "id"}
            or value["type"] not in ("node", "way", "relation")
            or not isinstance(value["id"], int)
            or value["id"] < 1
        ):
            raise _Invalid('must be {"type": node|way|relation, "id": a positive integer}')
        return {"type": value["type"], "id": value["id"]}
    raise _Invalid("is not a field suggestions may change")


def current_value(field: str, obj: Vendor | VendorLocation) -> Any:
    if field == "osm":
        assert isinstance(obj, VendorLocation)
        return {"type": obj.osm_type, "id": obj.osm_id} if obj.osm_type else None
    return getattr(obj, field)


# --- intake ------------------------------------------------------------------


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


@dataclass(frozen=True)
class Stored:
    batch_id: uuid.UUID
    stored: int
    collapsed: int


async def intake(
    db: AsyncSession, batch: SuggestionBatchIn, *, token_id: uuid.UUID | None
) -> Stored:
    if len(batch.items) > MAX_ITEMS:
        raise ApiError(
            413,
            "batch_too_large",
            f"At most {MAX_ITEMS} suggestions per request.",
            {"limit": MAX_ITEMS, "items": len(batch.items)},
        )
    vendor_keys = {i.key for i in batch.items if i.target == "vendor"}
    location_keys = {i.key for i in batch.items if i.target == "location"}
    vendors = {
        v.slug: v
        for v in (await db.execute(select(Vendor).where(Vendor.slug.in_(vendor_keys))))
        .scalars()
        .all()
    }
    locations = {
        loc.key: loc
        for loc in (
            await db.execute(select(VendorLocation).where(VendorLocation.key.in_(location_keys)))
        )
        .unique()
        .scalars()
        .all()
    }
    errors: list[dict[str, Any]] = []
    rows: list[tuple[SuggestionIn, Vendor | VendorLocation, Any, Any]] = []
    for index, item in enumerate(batch.items):
        where = {"item": index, "key": item.key, "field": item.field}
        obj: Vendor | VendorLocation | None = (
            vendors.get(item.key) if item.target == "vendor" else locations.get(item.key)
        )
        allowed = VENDOR_FIELDS if item.target == "vendor" else LOCATION_FIELDS
        try:
            if obj is None:
                raise _Invalid(f"no {item.target} has the key {item.key}")
            if item.field not in allowed:
                raise _Invalid(f"a {item.target} has no {item.field} to suggest")
            proposed = normalize(item.field, item.proposed)
            expected = None if item.expected is None else normalize(item.field, item.expected)
            _url(item.source_url, limit=2000)
            if item.confidence is not None and not Decimal(0) <= item.confidence <= Decimal(1):
                raise _Invalid("confidence must be between 0 and 1")
        except _Invalid as exc:
            errors.append({**where, "problem": str(exc)})
            continue
        rows.append((item, obj, expected, proposed))
    if errors:
        raise ApiError(
            422,
            "invalid_suggestions",
            f"{len(errors)} of {len(batch.items)} suggestions are not valid; none were stored.",
            {"errors": errors[:50]},
        )

    targets = [obj.id for _, obj, _, _ in rows]
    existing = (
        await db.execute(
            select(
                VendorSuggestion.vendor_id,
                VendorSuggestion.vendor_location_id,
                VendorSuggestion.field,
                VendorSuggestion.proposed_value,
            ).where(
                VendorSuggestion.status == "pending",
                (VendorSuggestion.vendor_id.in_(targets))
                | (VendorSuggestion.vendor_location_id.in_(targets)),
            )
        )
    ).all()
    # JSONB reorders object keys, so signatures compare canonical JSON.
    seen = {(v or loc, f, _canon(p)) for v, loc, f, p in existing}
    fresh: list[VendorSuggestion] = []
    batch_id = uuid.uuid4()
    for item, obj, expected, proposed in rows:
        sig = (obj.id, item.field, _canon(proposed))
        if sig in seen or current_value(item.field, obj) == proposed:
            continue  # a duplicate of a pending suggestion, or already true
        seen.add(sig)
        fresh.append(
            VendorSuggestion(
                batch_id=batch_id,
                target=item.target,
                vendor_id=obj.id if item.target == "vendor" else None,
                vendor_location_id=obj.id if item.target == "location" else None,
                field=item.field,
                old_value=expected,
                proposed_value=proposed,
                source_url=item.source_url.strip(),
                evidence=item.evidence,
                tool=batch.tool.strip(),
                tool_version=batch.tool_version.strip(),
                confidence=item.confidence,
                created_by_token_id=token_id,
                status="pending",
            )
        )
    pending = await db.scalar(
        select(func.count())
        .select_from(VendorSuggestion)
        .where(VendorSuggestion.status == "pending")
    )
    if (pending or 0) + len(fresh) > MAX_PENDING:
        raise ApiError(
            409,
            "too_many_pending",
            f"{pending} suggestions already wait for review; the limit is {MAX_PENDING}. "
            "Review some first.",
            {"pending": pending, "limit": MAX_PENDING},
        )
    db.add_all(fresh)
    await db.commit()
    return Stored(batch_id=batch_id, stored=len(fresh), collapsed=len(batch.items) - len(fresh))


# --- review ------------------------------------------------------------------


def _domain(url: str) -> str:
    host = urlsplit(url).hostname or url
    return host.removeprefix("www.")


async def _targets(
    db: AsyncSession, suggestions: list[VendorSuggestion]
) -> tuple[dict[uuid.UUID, Vendor], dict[uuid.UUID, VendorLocation]]:
    location_ids = {s.vendor_location_id for s in suggestions if s.vendor_location_id}
    locations = {
        loc.id: loc
        for loc in (
            await db.execute(select(VendorLocation).where(VendorLocation.id.in_(location_ids)))
        )
        .unique()
        .scalars()
    }
    vendor_ids = {s.vendor_id for s in suggestions if s.vendor_id} | {
        loc.vendor_id for loc in locations.values()
    }
    vendors = {
        v.id: v
        for v in (await db.execute(select(Vendor).where(Vendor.id.in_(vendor_ids)))).scalars()
    }
    return vendors, locations


def _view(
    s: VendorSuggestion,
    vendors: dict[uuid.UUID, Vendor],
    locations: dict[uuid.UUID, VendorLocation],
) -> SuggestionOut:
    location = locations.get(s.vendor_location_id) if s.vendor_location_id else None
    vendor = vendors[location.vendor_id if location is not None else s.vendor_id]  # type: ignore[index]
    obj: Vendor | VendorLocation = location if location is not None else vendor
    current = current_value(s.field, obj)
    return SuggestionOut(
        id=s.id,
        batch_id=s.batch_id,
        target=s.target,  # type: ignore[arg-type]
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        location_id=location.id if location else None,
        location_name=location.name if location else None,
        field=s.field,  # type: ignore[arg-type]
        expected=s.old_value,
        current=current,
        proposed=s.proposed_value,
        stale=s.status == "stale" or (s.status == "pending" and current != s.old_value),
        status=s.status,  # type: ignore[arg-type]
        source_url=s.source_url,
        source_domain=_domain(s.source_url),
        evidence=s.evidence,
        tool=s.tool,
        tool_version=s.tool_version,
        created_at=s.created_at,
    )


def _for_vendor(vendor_id: uuid.UUID) -> Any:
    return (VendorSuggestion.vendor_id == vendor_id) | VendorSuggestion.vendor_location_id.in_(
        select(VendorLocation.id).where(VendorLocation.vendor_id == vendor_id)
    )


async def awaiting(db: AsyncSession, *, vendor_id: uuid.UUID | None = None) -> list[SuggestionOut]:
    stmt = select(VendorSuggestion).where(VendorSuggestion.status.in_(AWAITING))
    if vendor_id is not None:
        stmt = stmt.where(_for_vendor(vendor_id))
    found = list((await db.execute(stmt.order_by(VendorSuggestion.created_at))).scalars())
    vendors, locations = await _targets(db, found)
    views = [_view(s, vendors, locations) for s in found]
    # Grouped by vendor, then location (the vendor's own rows first), then field.
    views.sort(key=lambda v: (v.vendor_name, v.location_name or "", v.field, v.created_at))
    return views


async def summary(db: AsyncSession) -> SuggestionSummary:
    views = await awaiting(db)
    by_vendor: dict[uuid.UUID, SuggestionVendorCount] = {}
    for v in views:
        entry = by_vendor.setdefault(
            v.vendor_id, SuggestionVendorCount(vendor_id=v.vendor_id, name=v.vendor_name, count=0)
        )
        entry.count += 1
    return SuggestionSummary(
        count=len(views),
        tools=sorted({v.tool for v in views}),
        vendors=sorted(by_vendor.values(), key=lambda e: (-e.count, e.name)),
    )


async def oldest_awaiting(db: AsyncSession) -> tuple[int, datetime | None, list[str]]:
    """For the inbox: how many wait, since when, and from which tools."""
    row = (
        await db.execute(
            select(func.count(), func.min(VendorSuggestion.created_at)).where(
                VendorSuggestion.status.in_(AWAITING)
            )
        )
    ).one()
    tools = (
        await db.execute(
            select(VendorSuggestion.tool)
            .where(VendorSuggestion.status.in_(AWAITING))
            .distinct()
            .order_by(VendorSuggestion.tool)
        )
    ).scalars()
    return row[0], row[1], list(tools)


async def _load_for_decision(db: AsyncSession, suggestion_id: uuid.UUID) -> VendorSuggestion:
    s = (
        await db.execute(
            select(VendorSuggestion)
            .where(VendorSuggestion.id == suggestion_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if s is None:
        raise ApiError(404, "not_found", "No such suggestion.")
    if s.status not in AWAITING:
        raise ApiError(409, "already_decided", f"This suggestion was already {s.status}.")
    return s


def _decide(s: VendorSuggestion, user: AppUser, status: str, now: datetime) -> None:
    s.status, s.decided_by, s.decided_at = status, user.id, now


async def _apply(
    db: AsyncSession, s: VendorSuggestion, obj: Vendor | VendorLocation, now: datetime
) -> None:
    """Write the proposed value and record where it came from."""
    value = s.proposed_value
    if s.field == "osm":
        assert isinstance(obj, VendorLocation)
        obj.osm_type, obj.osm_id = value["type"], value["id"]
    else:
        setattr(obj, s.field, value)
    record = {
        "source": f"enriched:{s.tool}",
        "ref": s.source_url,
        "checked_at": now.isoformat(),
        "imported": value,
    }
    obj.field_source = {**(obj.field_source or {}), s.field: record}
    await geo._flush(db)


async def _target_of(db: AsyncSession, s: VendorSuggestion) -> Vendor | VendorLocation:
    if s.vendor_location_id is not None:
        return (
            (
                await db.execute(
                    select(VendorLocation)
                    .where(VendorLocation.id == s.vendor_location_id)
                    .with_for_update(of=VendorLocation)
                )
            )
            .unique()
            .scalar_one()
        )
    return (
        await db.execute(select(Vendor).where(Vendor.id == s.vendor_id).with_for_update())
    ).scalar_one()


async def accept(
    db: AsyncSession, user: AppUser, suggestion_id: uuid.UUID, *, override: bool = False
) -> tuple[str, SuggestionOut]:
    """Apply a suggestion. If its field changed since it was proposed, it is marked
    stale and nothing is written, unless the person chose to override."""
    now = datetime.now(UTC)
    s = await _load_for_decision(db, suggestion_id)
    obj = await _target_of(db, s)
    changed = s.status == "stale" or current_value(s.field, obj) != s.old_value
    if changed and not override:
        _decide(s, user, "stale", now)
        outcome = "stale"
    else:
        await _apply(db, s, obj, now)
        _decide(s, user, "accepted", now)
        outcome = "accepted"
    try:
        await geo._flush(db)
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    vendors, locations = await _targets(db, [s])
    return outcome, _view(s, vendors, locations)


async def reject(db: AsyncSession, user: AppUser, suggestion_id: uuid.UUID) -> SuggestionOut:
    s = await _load_for_decision(db, suggestion_id)
    _decide(s, user, "rejected", datetime.now(UTC))
    await db.commit()
    vendors, locations = await _targets(db, [s])
    return _view(s, vendors, locations)


async def accept_all(db: AsyncSession, user: AppUser, vendor_id: uuid.UUID) -> tuple[int, int]:
    """Accept every pending suggestion for a vendor whose field is still as expected.
    Stale ones are left for a person to decide one by one (design D8)."""
    ids = list(
        (
            await db.execute(
                select(VendorSuggestion.id)
                .where(VendorSuggestion.status.in_(AWAITING), _for_vendor(vendor_id))
                .order_by(VendorSuggestion.created_at)
            )
        ).scalars()
    )
    if not ids and await db.get(Vendor, vendor_id) is None:
        raise ApiError(404, "not_found", "No such vendor.")
    accepted = skipped = 0
    now = datetime.now(UTC)
    try:
        for suggestion_id in ids:
            s = await _load_for_decision(db, suggestion_id)
            obj = await _target_of(db, s)
            if s.status == "stale" or current_value(s.field, obj) != s.old_value:
                skipped += 1
                continue
            await _apply(db, s, obj, now)
            _decide(s, user, "accepted", now)
            accepted += 1
        await geo._flush(db)
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return accepted, skipped
