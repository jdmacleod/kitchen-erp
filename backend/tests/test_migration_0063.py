"""Migration 0063 adds kitchen supply lines, and its downgrade keeps the person's
choice as near as the old schema can: a supply line waits in To identify again,
and its alias becomes an ignore, so the wording is still not asked about.
Every store and wording here is invented.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from tests.conftest import run_alembic
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase


async def _engine_reset() -> None:
    from app.core.db import dispose_engine

    await dispose_engine()


async def test_supply_lines_round_trip(admin_client, admin, owner_conn):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    purchase_id = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "CEDAR PLANKS 2PK", "line_total": "7.00"}]
    )
    line_id = await owner_conn.fetchval(
        "SELECT id FROM purchase_line WHERE purchase_id = $1", uuid.UUID(purchase_id)
    )
    await owner_conn.execute(
        "UPDATE purchase_line SET raw_text_norm = 'CEDAR PLANKS 2PK', resolution = 'supply', "
        "supply_category = 'fuel', resolved_by = $2 WHERE id = $1",
        line_id,
        admin.id,
    )
    await owner_conn.execute(
        "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, supply_category, "
        "confirmed_count, last_seen_at) VALUES ($1, $2, 'CEDAR PLANKS 2PK', 'supply', 'fuel', "
        "1, now())",
        uuid.uuid4(),
        uuid.UUID(loc["vendor"]["id"]),
    )

    run_alembic("downgrade", "0062")
    await _engine_reset()
    try:
        line = await owner_conn.fetchrow(
            "SELECT resolution, resolved_by FROM purchase_line WHERE id = $1", line_id
        )
        assert dict(line) == {"resolution": "unmatched", "resolved_by": None}
        alias = await owner_conn.fetchrow("SELECT disposition, confirmed_count FROM receipt_alias")
        assert dict(alias) == {"disposition": "ignore", "confirmed_count": 1}
        columns = await owner_conn.fetch(
            "SELECT table_name FROM information_schema.columns "
            "WHERE column_name = 'supply_category'"
        )
        assert columns == []
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()

    # Back at the head the constraints hold: a supply needs its category.
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "UPDATE purchase_line SET resolution = 'supply' WHERE id = $1", line_id
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "UPDATE purchase_line SET supply_category = 'cleaning' WHERE id = $1", line_id
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute("UPDATE receipt_alias SET disposition = 'supply'")
