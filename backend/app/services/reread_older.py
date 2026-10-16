"""Drafts read with older line rules, re-checked together (04, 2S; issue 382).

A draft is *read with older rules* when its latest reading kept the reader's
answer (so Re-check works on it), that reading was assessed under a
``rules_version`` below :data:`assess.LINE_RULES_VERSION` (a reading without one
is older than 1), and no re-read made under today's rules exists for it. Such a
re-read, whatever became of it (no changes, applied, discarded), is the record
that the draft was checked, so a draft whose re-check changed nothing stops
being counted. A draft with a re-read open is already in the reading line or the
Re-read row and is not counted again.

Committed and reopened purchases are left out: applying a re-read to one reopens
it or moves prices a person already committed, so it stays a one-receipt choice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.ingest import assess
from app.models import AppUser
from app.services import reread

# Drafts only, from a finished read (still reading, failed or discarded jobs have
# rows of their own), whose latest ``lines`` result kept ``read_lines``. Drafts are
# few, and each looks up only its own job's results through the job_id index.
_OLDER_SQL = """
    FROM purchase p
    JOIN ingest_job j ON j.purchase_id = p.id
    CROSS JOIN LATERAL (
        SELECT r.output FROM ingest_stage_result r
        WHERE r.job_id = j.id AND r.stage = 'lines'
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT 1
    ) AS l
    WHERE p.status = 'draft' AND p.source = 'receipt'
      AND j.status IN ('needs_review', 'done')
      AND l.output ? 'read_lines'
      AND coalesce((l.output ->> 'rules_version')::int, 0) < :version
      AND NOT EXISTS (
          SELECT 1 FROM line_reread x
          WHERE x.purchase_id = p.id
            AND (x.status IN ('pending', 'running', 'ready')
                 OR coalesce((x.proposal ->> 'rules_version')::int, 0) >= :version)
      )
"""

_COUNT_SQL = text(f"SELECT count(*) AS n, min(p.created_at) AS oldest_at {_OLDER_SQL}")
_IDS_SQL = text(f"SELECT p.id {_OLDER_SQL} ORDER BY p.purchased_at, p.id")

# Re-checks ready with changes on drafts, oldest receipt first: what the page lists.
_READY_SQL = text(
    """
    SELECT x.id AS reread_id, p.id AS purchase_id, p.purchased_at, v.name AS store,
           x.proposal -> 'counts' AS counts,
           x.proposal -> 'after' -> 'reconciliation' AS after
    FROM line_reread x
    JOIN purchase p ON p.id = x.purchase_id
    LEFT JOIN vendor_location vl ON vl.id = p.vendor_location_id
    LEFT JOIN vendor v ON v.id = vl.vendor_id
    WHERE x.status = 'ready' AND p.status = 'draft'
    ORDER BY p.purchased_at, p.id
    """
)


def _params() -> dict[str, int]:
    return {"version": assess.LINE_RULES_VERSION}


async def count(db: AsyncSession) -> tuple[int, datetime | None]:
    """How many drafts were read with older rules, and when the oldest was made."""
    row = (await db.execute(_COUNT_SQL, _params())).mappings().one()
    return row["n"], row["oldest_at"]


async def ready(db: AsyncSession) -> list[dict[str, Any]]:
    """Drafts whose re-read is ready with changes, each with what would change."""
    found = []
    for r in (await db.execute(_READY_SQL)).mappings():
        after = r["after"] or {}
        found.append(
            {
                "reread_id": r["reread_id"],
                "purchase_id": r["purchase_id"],
                "purchased_at": r["purchased_at"],
                "store": r["store"],
                "counts": r["counts"],
                "adds_up": after.get("checked", False) and not after.get("mismatch", False),
                "off_by": after.get("difference") if after.get("mismatch") else None,
            }
        )
    return found


async def overview(db: AsyncSession) -> dict[str, Any]:
    older, _ = await count(db)
    return {
        "rules_version": assess.LINE_RULES_VERSION,
        "older": older,
        "ready": await ready(db),
        "checked": None,
    }


async def recheck(db: AsyncSession, user: AppUser) -> dict[str, Any]:
    """Re-check every draft read with older rules, oldest receipt first.

    Each is the Re-check a person would ask for on that receipt (``reread.request``
    in check mode): no model, a proposal applied only by a person. One that
    changes nothing closes as ``no_changes`` and stops being counted. A draft
    that another request opened a re-read for, or that changed under the loop,
    is skipped and counted, never failed.
    """
    ids: list[uuid.UUID] = list((await db.execute(_IDS_SQL, _params())).scalars())
    checked = {"ready": 0, "no_changes": 0, "skipped": 0}
    for purchase_id in ids:
        try:
            made = await reread.request(db, user, purchase_id, "check")
        except ApiError:
            await db.rollback()
            await db.refresh(user)  # the rollback expired it; request reads its id
            checked["skipped"] += 1
            continue
        checked[made.status] += 1
    return {**(await overview(db)), "checked": checked}
