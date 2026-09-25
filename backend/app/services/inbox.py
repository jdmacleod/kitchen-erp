"""The unified inbox: everything the system could not finish on its own.

One list, oldest first, computed from existing tables; there is no inbox table.
Spec: docs/spec/09-information-architecture.md, "Unified inbox".

    kind             source                                         one row per
    ---------------  ---------------------------------------------  -----------------
    receipt          draft or reviewed purchases, excluding those   purchase
                     whose ingest job is still reading or failed
    receipt_failed   failed ingest jobs                             job
    identify         resolution.to_identify (committed, unmatched)  one aggregate row
    bridge           pricebook.needs_bridge (failed normalization)  product

Receipts still being read are not items: they come back as ``reading`` so Home can
show one line above the list.

The identify and bridge kinds reuse the queries behind their own pages rather
than restating them, so the inbox can never count something those pages do not
show. Any kind failing fails the whole request: a partial inbox that looks
complete would say "nothing needs you" when that is not known (D6).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.inbox import InboxItem, InboxOut, InboxReading
from app.services import pricebook, resolution

log = get_logger(__name__)

# A draft whose job is still reading is counted under ``reading``; a draft whose job
# failed is the ``receipt_failed`` row. Either way it is not also a receipt row. Once
# the purchase is past draft, its own status decides and the failed job is history.
_RECEIPTS_SQL = text(
    """
    SELECT p.id, p.status, p.source, p.purchased_at, p.created_at,
           p.vendor_location_id IS NULL AS needs_location,
           count(pl.id) FILTER (WHERE pl.line_kind = 'item') AS item_lines
    FROM purchase p
    LEFT JOIN purchase_line pl ON pl.purchase_id = p.id
    WHERE p.status IN ('draft', 'reviewed')
      AND NOT EXISTS (
          SELECT 1 FROM ingest_job j
          WHERE j.purchase_id = p.id
            AND (j.status IN ('pending', 'running') OR (j.status = 'failed' AND p.status = 'draft'))
      )
    GROUP BY p.id
    """
)

# A failed read needs a person only while its purchase is missing or still a draft:
# committing the draft directly settles it without touching the job.
_FAILED_SQL = text(
    """
    SELECT j.id, j.created_at, j.last_error
    FROM ingest_job j
    LEFT JOIN purchase p ON p.id = j.purchase_id
    WHERE j.status = 'failed' AND (p.id IS NULL OR p.status = 'draft')
    """
)

_READING_SQL = text(
    """
    SELECT count(*) AS n, min(created_at) AS oldest_at
    FROM ingest_job WHERE status IN ('pending', 'running')
    """
)


def _day(moment: datetime) -> str:
    """A short date such as Sep 24, in the household's timezone: the day the person remembers."""
    local = moment.astimezone(ZoneInfo(get_settings().household_timezone))
    return f"{local:%b} {local.day}"


def _lines(n: int) -> str:
    return f"{n} line" if n == 1 else f"{n} lines"


async def _receipts(db: AsyncSession) -> list[InboxItem]:
    items = []
    for r in (await db.execute(_RECEIPTS_SQL)).mappings():
        noun = "receipt" if r["source"] == "receipt" else "purchase"
        if r["needs_location"]:
            detail = f"{_lines(r['item_lines'])} read. Choose where you shopped to commit it."
            action = "Choose location"
        elif r["status"] == "reviewed":
            detail = "Reopened for a correction. Review it and commit it again."
            action = "Review"
        else:
            detail = f"{_lines(r['item_lines'])} ready to review and commit."
            action = "Review"
        items.append(
            InboxItem(
                kind="receipt",
                title=f"Finish the {_day(r['purchased_at'])} {noun}",
                detail=detail,
                action_label=action,
                action_route=f"/purchases/{r['id']}",
                created_at=r["created_at"],
            )
        )
    return items


async def _failed_reads(db: AsyncSession) -> list[InboxItem]:
    return [
        InboxItem(
            kind="receipt_failed",
            title="A receipt couldn't be read",
            detail="Retry it, or enter it by hand.",
            action_label="Open receipt",
            # The job itself: the Receipts list shows only the newest jobs.
            action_route=f"/receipts?job={r['id']}",
            created_at=r["created_at"],
            error_code=r["last_error"],
        )
        for r in (await db.execute(_FAILED_SQL)).mappings()
    ]


async def _identify(db: AsyncSession) -> list[InboxItem]:
    groups = await resolution.to_identify(db)
    if not groups:
        return []
    lines = sum(g["line_count"] for g in groups)
    oldest = min(
        datetime.fromisoformat(line["purchased_at"]) for g in groups for line in g["lines"]
    )
    noun = "receipt line" if lines == 1 else "receipt lines"
    return [
        InboxItem(
            kind="identify",
            title=f"{lines} {noun} to identify",
            detail="Match them once and each vendor's alias is learned for next time.",
            action_label="Review lines",
            action_route="/to-identify",
            created_at=oldest,
        )
    ]


# What each normalization failure needs, and where it is fixed. Mirrors
# bridgeFixLink in frontend/src/api/pricebook.ts.
_BRIDGE_FIX = {
    "no_density": ("a density", "Add density", "ingredient", "#density-heading"),
    "unknown_measure": ("a measure", "Add measure", "ingredient", "#measures-heading"),
    "no_pack": ("a pack size", "Set pack", "product", ""),
    "no_qty": ("a quantity", "Check product", "product", ""),
}


async def _bridges(db: AsyncSession) -> list[InboxItem]:
    # needs_bridge groups by product *and* failure reason; the inbox wants one row
    # per product, so a product failing two ways is merged here.
    by_product: dict[uuid.UUID, dict] = {}
    for row in await pricebook.needs_bridge(db):
        entry = by_product.setdefault(
            row["product_id"], {"row": row, "statuses": [], "first": row["first_observed_at"]}
        )
        entry["statuses"].append(row["status"])
        entry["first"] = min(entry["first"], row["first_observed_at"])
    items = []
    for entry in by_product.values():
        row = entry["row"]
        order = list(_BRIDGE_FIX)
        statuses = sorted(
            entry["statuses"], key=lambda s: order.index(s) if s in order else len(order)
        )
        needs = [_BRIDGE_FIX[s][0] for s in statuses if s in _BRIDGE_FIX]
        _, label, target, anchor = _BRIDGE_FIX.get(statuses[0], _BRIDGE_FIX["no_qty"])
        route = (
            f"/ingredients/{row['ingredient_id']}{anchor}"
            if target == "ingredient"
            else f"/products/{row['product_id']}"
        )
        title = f"{row['brand']} {row['name']}" if row["brand"] else row["name"]
        items.append(
            InboxItem(
                kind="bridge",
                title=title,
                detail=f"Its prices can't be compared until it has {' and '.join(needs)}.",
                action_label=label,
                action_route=route,
                created_at=entry["first"],
            )
        )
    return items


async def _reading(db: AsyncSession) -> InboxReading:
    row = (await db.execute(_READING_SQL)).mappings().one()
    oldest = row["oldest_at"]
    stall = timedelta(minutes=get_settings().ingest_stall_minutes)
    return InboxReading(
        count=row["n"],
        oldest_at=oldest,
        stalled=oldest is not None and datetime.now(UTC) - oldest > stall,
    )


_KINDS: list[tuple[str, Callable[[AsyncSession], Awaitable[list[InboxItem]]]]] = [
    ("receipt", _receipts),
    ("receipt_failed", _failed_reads),
    ("identify", _identify),
    ("bridge", _bridges),
]


async def inbox(db: AsyncSession) -> InboxOut:
    items: list[InboxItem] = []
    counts: dict[str, int] = {}
    for kind, compute in _KINDS:
        try:
            found = await compute(db)
        except Exception:
            # Say which kind failed, then let the request fail: never a partial list.
            log.exception("inbox.kind_failed", extra={"kind": kind})
            raise
        counts[kind] = len(found)
        items.extend(found)
    items.sort(key=lambda item: item.created_at)
    reading = await _reading(db)
    log.info("inbox", extra={"counts": counts, "reading": reading.count})
    return InboxOut(items=items, reading=reading)
