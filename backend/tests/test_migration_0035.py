"""Migration 0035 closes refreshes queued for stores that may not be fetched (#264).

Seeded at 0034 with invented stores: one ``capture_only`` with an open refresh,
one ``server_fetch`` with an open refresh, one paused with an open refresh, and an
open page request for a product (not a listing refresh). The upgrade closes the
first and third; a downgrade reopens exactly those.
"""

from __future__ import annotations

import uuid

import asyncpg

from tests.conftest import run_alembic
from tests.pricebook_helpers import make_location, make_product


async def _engine_reset() -> None:
    from app.core.db import dispose_engine

    await dispose_engine()


async def _open_refresh(
    conn: asyncpg.Connection, vendor_id: str, product_id: str, url: str
) -> uuid.UUID:
    listing_id, request_id = uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        "INSERT INTO vendor_listing (id, vendor_id, product_id, canonical_url, title, "
        "last_captured_at, status) VALUES ($1, $2, $3, $4, 'Barley', now(), 'active')",
        listing_id,
        uuid.UUID(vendor_id),
        uuid.UUID(product_id),
        url,
    )
    await conn.execute(
        "INSERT INTO lookup_request (id, kind, product_id, listing_id, value) "
        "VALUES ($1, 'page', $2, $3, $4)",
        request_id,
        uuid.UUID(product_id),
        listing_id,
        url,
    )
    return request_id


async def _status(conn: asyncpg.Connection, request_id: uuid.UUID) -> str:
    return await conn.fetchval("SELECT status FROM lookup_request WHERE id = $1", request_id)


async def test_refreshes_for_unfetchable_stores_are_closed_and_reopened(admin_client, owner_conn):
    clip_only = await make_location(admin_client, "Willow Larder", "Willow Larder")
    fetched = await make_location(admin_client, "Larkspur Grocer", "Larkspur Grocer")
    paused = await make_location(admin_client, "Fennel Row", "Fennel Row")
    product = await make_product(admin_client, "Barley", "Pearl barley bag")

    run_alembic("downgrade", "0034")
    await _engine_reset()
    try:
        await owner_conn.execute(
            "UPDATE vendor SET fetch_policy = 'server_fetch' WHERE id = ANY($1::uuid[])",
            [uuid.UUID(fetched["vendor"]["id"]), uuid.UUID(paused["vendor"]["id"])],
        )
        await owner_conn.execute(
            "UPDATE vendor SET refresh_paused_until = now() + interval '7 days' WHERE id = $1",
            uuid.UUID(paused["vendor"]["id"]),
        )
        closed_one = await _open_refresh(
            owner_conn, clip_only["vendor"]["id"], product["id"], "https://willow.example.test/p/1"
        )
        kept = await _open_refresh(
            owner_conn, fetched["vendor"]["id"], product["id"], "https://larkspur.example.test/p/1"
        )
        closed_two = await _open_refresh(
            owner_conn, paused["vendor"]["id"], product["id"], "https://fennel.example.test/p/1"
        )
        product_page = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO lookup_request (id, kind, product_id, value) "
            "VALUES ($1, 'page', $2, 'https://willow.example.test/p/2')",
            product_page,
            uuid.UUID(product["id"]),
        )

        run_alembic("upgrade", "0035")
        assert await _status(owner_conn, closed_one) == "closed"
        assert await _status(owner_conn, closed_two) == "closed"
        assert await _status(owner_conn, kept) == "open"
        assert await _status(owner_conn, product_page) == "open"

        run_alembic("downgrade", "0034")
        for request_id in (closed_one, closed_two, kept, product_page):
            assert await _status(owner_conn, request_id) == "open"
        table = await owner_conn.fetchval("SELECT to_regclass('lookup_refresh_closed_0035')")
        assert table is None
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()
