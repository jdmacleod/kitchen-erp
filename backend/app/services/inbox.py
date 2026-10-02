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
    vendor_suggest.. vendor_suggestion awaiting a decision (1F)     one aggregate row
    link             ingredients still unreviewed against the       one aggregate row
                     standard list (1G)
    usda             usda_review.review_list, when linking is done  one aggregate row
                     or a linked ingredient has suggestions (1G)
    new_product      pending new-product proposals (2L)             one aggregate row
    product_update   pending product-update proposals (2N)          one aggregate row
    posted_prices    refreshed posted prices awaiting a person (2N) one aggregate row

Receipts, product photos and product pages still being read are not items: they
come back as ``reading`` so Home can show one line above the list.

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

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.catalog import Ingredient
from app.schemas.inbox import InboxItem, InboxOut, InboxReading
from app.services import (
    lookups,
    pricebook,
    proposals,
    resolution,
    usda_review,
    vendor_suggestions,
)

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
    LEFT JOIN purchase_line pl ON pl.purchase_id = p.id AND pl.removed_at IS NULL
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
    SELECT count(*) AS n, min(created_at) AS oldest_at,
           (SELECT max(created_at) FROM ingest_stage_result) AS last_progress_at
    FROM ingest_job WHERE status IN ('pending', 'running')
    """
)

# Product captures being identified (2L): one per capture, by how it arrived.
_PRODUCT_READING_SQL = text(
    """
    SELECT count(*) FILTER (WHERE c.channel = 'photo') AS photos,
           count(*) FILTER (WHERE c.channel IN ('clip', 'paste_url')) AS pages,
           min(j.created_at) AS oldest_at,
           (SELECT max(created_at) FROM product_stage_result) AS last_progress_at
    FROM product_job j JOIN product_capture c ON c.id = j.product_capture_id
    WHERE j.status IN ('pending', 'running')
    """
)


def _day(moment: datetime) -> str:
    """A short date such as Sep 24, in the household's timezone: the day the person remembers.

    Another year's date carries its year (Jul 30, 2020). Without it an old
    receipt, or one whose year was misread, looked like one from this summer.
    """
    zone = ZoneInfo(get_settings().household_timezone)
    local = moment.astimezone(zone)
    if local.year != datetime.now(zone).year:
        return f"{local:%b} {local.day}, {local.year}"
    return f"{local:%b} {local.day}"


def _lines(n: int) -> str:
    return f"{n} line" if n == 1 else f"{n} lines"


async def _receipts(db: AsyncSession) -> list[InboxItem]:
    items = []
    for r in (await db.execute(_RECEIPTS_SQL)).mappings():
        noun = "receipt" if r["source"] == "receipt" else "purchase"
        if r["item_lines"] == 0 and r["status"] != "reviewed":
            # Choosing a location is not the job when nothing was read: the lines
            # are, from the receipt image beside them.
            detail = "No lines could be read. Add them from the receipt image."
            action = "Add lines"
        elif r["needs_location"]:
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
                action_route=f"/shop/purchases/{r['id']}",
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
            action_route=f"/shop/receipts?job={r['id']}",
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
            action_route="/shop/receipts/identify",
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
            f"/catalog/ingredients/{row['ingredient_id']}{anchor}"
            if target == "ingredient"
            else f"/catalog/products/{row['product_id']}"
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


async def _suggestions(db: AsyncSession) -> list[InboxItem]:
    count, oldest, tools = await vendor_suggestions.oldest_awaiting(db)
    if not count or oldest is None:
        return []
    noun = "vendor suggestion" if count == 1 else "vendor suggestions"
    source = f"From {', '.join(tools)}. " if tools else ""
    return [
        InboxItem(
            kind="vendor_suggestions",
            title=f"{count} {noun} to review",
            detail=f"{source}Nothing changes until you accept one.",
            action_label="Review",
            action_route="/catalog/vendors?suggestions=1",
            created_at=oldest,
        )
    ]


async def _link(db: AsyncSession) -> list[InboxItem]:
    count, oldest = (
        await db.execute(
            select(func.count(), func.min(Ingredient.created_at)).where(
                Ingredient.active, Ingredient.reconcile_state == "unreviewed"
            )
        )
    ).one()
    if not count or oldest is None:
        return []
    noun = "ingredient" if count == 1 else "ingredients"
    return [
        InboxItem(
            kind="link",
            title=f"{count} {noun} to link to the standard list",
            detail="Give each its standard name so spellings and USDA data line up.",
            action_label="Review",
            action_route="/catalog/ingredients/link",
            created_at=oldest,
        )
    ]


async def _usda(db: AsyncSession) -> list[InboxItem]:
    # DV7: once linking is done, or as soon as a linked ingredient has suggestions.
    found = await usda_review.review_list(db)
    if not found.loaded or not found.groups:
        return []
    unreviewed = (
        await db.execute(
            select(func.count()).where(
                Ingredient.active, Ingredient.reconcile_state == "unreviewed"
            )
        )
    ).scalar_one()
    if unreviewed and not any(g.reconcile_state == "linked" for g in found.groups):
        return []
    oldest = (
        await db.execute(
            select(func.min(Ingredient.created_at)).where(
                Ingredient.id.in_([g.ingredient_id for g in found.groups])
            )
        )
    ).scalar_one()
    count = len(found.groups)
    noun = "ingredient has" if count == 1 else "ingredients have"
    return [
        InboxItem(
            kind="usda",
            title=f"{count} {noun} USDA densities to review",
            detail="Suggestions from USDA, saved as unconfirmed until you check them.",
            action_label="Review",
            action_route="/catalog/bridges#usda",
            created_at=oldest,
        )
    ]


async def _new_products(db: AsyncSession) -> list[InboxItem]:
    rows = await proposals.list_pending(db, limit=1000)
    new = [p for p in rows if p.kind == "new_product"]
    if not new:
        return []
    oldest = new[0]
    noun = "product" if len(new) == 1 else "products"
    return [
        InboxItem(
            kind="new_product",
            title=f"{len(new)} {noun} to review",
            detail="Check what was read, choose the ingredient, and accept or reject.",
            action_label="Review",
            action_route=f"/catalog/products/review/{oldest.id}",
            created_at=oldest.created_at,
        )
    ]


async def _product_updates(db: AsyncSession) -> list[InboxItem]:
    rows = await proposals.list_pending(db, limit=1000)
    updates = [p for p in rows if p.kind == "product_update"]
    if not updates:
        return []
    noun = "product update" if len(updates) == 1 else "product updates"
    return [
        InboxItem(
            kind="product_update",
            title=f"{len(updates)} {noun} to review",
            detail="The lookup helper found something new about products you have.",
            action_label="Review",
            action_route=f"/catalog/products/review/{updates[0].id}",
            created_at=updates[0].created_at,
        )
    ]


async def _posted_prices(db: AsyncSession) -> list[InboxItem]:
    changes = await lookups.pending_price_changes(db)
    if not changes:
        return []
    n = len(changes)
    return [
        InboxItem(
            kind="posted_prices",
            title=f"{n} posted {'price' if n == 1 else 'prices'} changed",
            detail="Accept the ones to record. Posted prices are not counted in cheapest.",
            action_label="Review",
            action_route="/catalog/products/posted-prices",
            created_at=changes[0]["created_at"],
        )
    ]


def _earliest(*moments: datetime | None) -> datetime | None:
    present = [m for m in moments if m is not None]
    return min(present) if present else None


def _latest(*moments: datetime | None) -> datetime | None:
    present = [m for m in moments if m is not None]
    return max(present) if present else None


async def _reading(db: AsyncSession) -> InboxReading:
    row = (await db.execute(_READING_SQL)).mappings().one()
    products = (await db.execute(_PRODUCT_READING_SQL)).mappings().one()
    oldest = _earliest(row["oldest_at"], products["oldest_at"])
    progress = _latest(row["last_progress_at"], products["last_progress_at"])
    stall = timedelta(minutes=get_settings().ingest_stall_minutes)
    now = datetime.now(UTC)
    # Lookups have their own line, only once the oldest has waited past the limit.
    waiting = await lookups.counts(db)
    overdue = waiting["since"] is not None and now - waiting["since"] > stall
    # Stalled means nothing is moving, not that something has waited: a batch of
    # receipts on a slow model keeps its last one waiting well past the limit
    # while the worker finishes a stage every minute or two.
    return InboxReading(
        count=row["n"],
        photos=products["photos"],
        pages=products["pages"],
        lookups_overdue=waiting["waiting"] if overdue else 0,
        lookups_since=waiting["since"] if overdue else None,
        oldest_at=oldest,
        stalled=oldest is not None
        and now - oldest > stall
        and (progress is None or now - progress > stall),
    )


_KINDS: list[tuple[str, Callable[[AsyncSession], Awaitable[list[InboxItem]]]]] = [
    ("receipt", _receipts),
    ("receipt_failed", _failed_reads),
    ("identify", _identify),
    ("bridge", _bridges),
    ("vendor_suggestions", _suggestions),
    ("link", _link),
    ("usda", _usda),
    ("new_product", _new_products),
    ("product_update", _product_updates),
    ("posted_prices", _posted_prices),
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
