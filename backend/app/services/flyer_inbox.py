"""The inbox's flyer rows (18, 2U-3): offers to match, and sources whose week went unread.

    kind            source                                          one row per
    --------------  ----------------------------------------------  -----------------
    flyer_offers    watched offers still suggested and valid (F13)  one aggregate row
    flyer_sources   a source's latest ad week ended without a read  source

Offers that aren't watched never make a row; they wait under the queue's "Other
offers".
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.inbox import InboxItem
from app.services import flyer_offers, flyers

OFFER_ROUTE = "/shop/offers/match"


async def offers(db: AsyncSession) -> list[InboxItem]:
    queue = await flyer_offers.offer_queue(db, flyers.today())
    watched = queue["watched"]
    if not watched:
        return []
    n = len(watched)
    noun = "flyer offer" if n == 1 else "flyer offers"
    return [
        InboxItem(
            kind="flyer_offers",
            title=f"{n} {noun} to match",
            detail="Offers for ingredients you watch. Match a name once and next week's "
            "links on its own.",
            action_label="Match offers",
            action_route=OFFER_ROUTE,
            created_at=min(g["first_seen_at"] for g in watched),
        )
    ]


# Each enabled source's latest ad week, and whether it ended without a read: the site
# refused it, or its last attempt is answered and none of them read the week.
_UNREAD = """
    WITH latest AS (
        SELECT DISTINCT ON (r.ad_source_id) r.ad_source_id, r.value AS week
        FROM lookup_request r WHERE r.kind = 'flyer'
        ORDER BY r.ad_source_id, r.value DESC
    ),
    weeks AS (
        SELECT l.ad_source_id, l.week,
               count(DISTINCT r.id) AS attempts,
               bool_or(r.status = 'open') AS waiting,
               bool_or(fr.status IN ('ok', 'empty')) AS read,
               bool_or(fr.status = 'not_allowed') AS refused_by_site,
               max(r.answered_at) AS ended_at
        FROM latest l
        JOIN lookup_request r ON r.ad_source_id = l.ad_source_id AND r.kind = 'flyer'
             AND r.value = l.week
        LEFT JOIN flyer_run fr ON fr.lookup_request_id = r.id
        GROUP BY l.ad_source_id, l.week
    )
    SELECT w.ad_source_id, w.ended_at, w.refused_by_site, v.name AS vendor_name,
           vl.name AS location_name, v.id AS vendor_id,
           (SELECT fr.reason FROM flyer_run fr JOIN lookup_request r ON r.id = fr.lookup_request_id
             WHERE r.ad_source_id = w.ad_source_id AND r.value = w.week
               AND fr.status IN ('failed', 'not_allowed')
             ORDER BY fr.created_at DESC LIMIT 1) AS reason,
           (SELECT a.outcome = 'refused' FROM lookup_answer a
              JOIN lookup_request r ON r.id = a.request_id
             WHERE r.ad_source_id = w.ad_source_id AND r.value = w.week
             ORDER BY a.created_at DESC LIMIT 1) AS last_refused
    FROM weeks w
    JOIN ad_source s ON s.id = w.ad_source_id
    JOIN vendor_location vl ON vl.id = s.vendor_location_id
    JOIN vendor v ON v.id = vl.vendor_id
    WHERE s.enabled AND vl.active AND NOT w.read AND NOT coalesce(w.waiting, false)
      AND (w.refused_by_site OR w.attempts >= :attempts)
    ORDER BY w.ended_at
"""


async def sources(db: AsyncSession) -> list[InboxItem]:
    rows = await db.execute(text(_UNREAD), {"attempts": flyers.ATTEMPTS_PER_WEEK})
    items = []
    for r in rows.mappings():
        if r["refused_by_site"]:
            detail = "The store's site doesn't allow reading its circular."
        elif r["last_refused"]:
            detail = "The adapter needs attention: its report didn't fit."
        else:
            detail = f"It couldn't be read: {(r['reason'] or 'no reason given').rstrip('.')}."
        items.append(
            InboxItem(
                kind="flyer_sources",
                title=f"{r['vendor_name']} {r['location_name']}: this week's circular wasn't read",
                detail=detail,
                action_label="Open the store",
                action_route=f"/catalog/vendors/{r['vendor_id']}",
                created_at=r["ended_at"],
            )
        )
    return items
