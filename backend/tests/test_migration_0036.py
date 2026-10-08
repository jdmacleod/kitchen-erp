"""Migration 0036 closes clip price lookups queued for stores that may not be fetched (#264).

Seeded at 0035 with invented stores: an open clip page request for a
``capture_only`` store, one for a ``server_fetch`` store, one for a paused store,
and an open image request for the ``capture_only`` store's clip. The upgrade
closes the first and third; a downgrade reopens exactly those.
"""

from __future__ import annotations

import json
import uuid

import asyncpg

from tests.conftest import run_alembic
from tests.pricebook_helpers import make_location


async def _engine_reset() -> None:
    from app.core.db import dispose_engine

    await dispose_engine()


async def _clip(conn: asyncpg.Connection, vendor_id: str, url: str) -> uuid.UUID:
    """A pending proposal from a clip, with its open page request; returns the request."""
    proposal_id, request_id = uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        "INSERT INTO product_proposal (id, kind, status, fields, match, listing) "
        "VALUES ($1, 'new_product', 'pending', '{}', '{}', $2::jsonb)",
        proposal_id,
        json.dumps({"vendor_id": vendor_id, "canonical_url": url}),
    )
    await conn.execute(
        "INSERT INTO lookup_request (id, kind, proposal_id, value) VALUES ($1, 'page', $2, $3)",
        request_id,
        proposal_id,
        url,
    )
    return request_id


async def _status(conn: asyncpg.Connection, request_id: uuid.UUID) -> str:
    return await conn.fetchval("SELECT status FROM lookup_request WHERE id = $1", request_id)


async def test_clip_lookups_for_unfetchable_stores_are_closed_and_reopened(
    admin_client, owner_conn
):
    clip_only = await make_location(admin_client, "Willow Larder", "Willow Larder")
    fetched = await make_location(admin_client, "Larkspur Grocer", "Larkspur Grocer")
    paused = await make_location(admin_client, "Fennel Row", "Fennel Row")

    run_alembic("downgrade", "0035")
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
        closed_one = await _clip(
            owner_conn, clip_only["vendor"]["id"], "https://willow.example.test/p/1"
        )
        kept = await _clip(owner_conn, fetched["vendor"]["id"], "https://larkspur.example.test/p/1")
        closed_two = await _clip(
            owner_conn, paused["vendor"]["id"], "https://fennel.example.test/p/1"
        )
        image = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO lookup_request (id, kind, proposal_id, value) "
            "SELECT $1, 'image', proposal_id, 'https://willow.example.test/i/1.jpg' "
            "FROM lookup_request WHERE id = $2",
            image,
            closed_one,
        )

        run_alembic("upgrade", "0036")
        assert await _status(owner_conn, closed_one) == "closed"
        assert await _status(owner_conn, closed_two) == "closed"
        assert await _status(owner_conn, kept) == "open"
        assert await _status(owner_conn, image) == "open"

        run_alembic("downgrade", "0035")
        for request_id in (closed_one, closed_two, kept, image):
            assert await _status(owner_conn, request_id) == "open"
        table = await owner_conn.fetchval("SELECT to_regclass('lookup_clip_closed_0036')")
        assert table is None
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()
