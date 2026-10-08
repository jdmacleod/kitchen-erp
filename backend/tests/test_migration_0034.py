"""Migration 0034 turns fetching on for stores the helper has read, and back (#264).

Two invented stores are seeded at revision 0033: the helper has read one's pages
(``found: true``) and only ever got nothing back from the other's. After the
upgrade the first may be fetched and the second may not; a downgrade returns the
first to ``capture_only`` unless a person has changed it since.
"""

from __future__ import annotations

import json
import uuid

import asyncpg

from tests.conftest import run_alembic
from tests.pricebook_helpers import make_location, make_product


async def _engine_reset() -> None:
    from app.core.db import dispose_engine

    await dispose_engine()


async def _refresh_answered(
    conn: asyncpg.Connection, vendor_id: str, product_id: str, url: str, found: bool
) -> None:
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
        "INSERT INTO lookup_request (id, kind, product_id, listing_id, value, status) "
        "VALUES ($1, 'page', $2, $3, $4, 'answered')",
        request_id,
        uuid.UUID(product_id),
        listing_id,
        url,
    )
    body = {"format": "kitchen-erp-products/1", "request_id": str(request_id), "found": found}
    await conn.execute(
        "INSERT INTO lookup_answer (id, request_id, body, outcome) "
        "VALUES (gen_random_uuid(), $1, $2::jsonb, 'no_change')",
        request_id,
        json.dumps(body),
    )


async def _policies(conn: asyncpg.Connection) -> dict[str, tuple[str, str | None]]:
    rows = await conn.fetch("SELECT name, fetch_policy, field_source FROM vendor")
    out = {}
    for r in rows:
        source = r["field_source"]
        source = json.loads(source) if isinstance(source, str) else source
        out[r["name"]] = (r["fetch_policy"], (source.get("fetch_policy") or {}).get("ref"))
    return out


async def test_fetching_is_turned_on_only_where_the_helper_read_a_page(admin_client, owner_conn):
    read = await make_location(admin_client, "Larkspur Grocer", "Larkspur Grocer")
    blocked = await make_location(admin_client, "Willow Larder", "Willow Larder")
    edited = await make_location(admin_client, "Fennel Row", "Fennel Row")
    product = await make_product(admin_client, "Barley", "Pearl barley bag")

    run_alembic("downgrade", "0033")
    await _engine_reset()
    try:
        for store, found, page in (
            (read, True, "https://larkspur.example.test/p/1"),
            (blocked, False, "https://willow.example.test/p/1"),
            (edited, True, "https://fennel.example.test/p/1"),
        ):
            await _refresh_answered(owner_conn, store["vendor"]["id"], product["id"], page, found)

        run_alembic("upgrade", "0034")
        assert await _policies(owner_conn) == {
            "Larkspur Grocer": ("server_fetch", "0034"),
            "Willow Larder": ("capture_only", None),
            "Fennel Row": ("server_fetch", "0034"),
        }
        failures = await owner_conn.fetchval("SELECT max(refresh_failures) FROM vendor")
        assert failures == 0

        # A person turns one off again; the downgrade leaves their choice alone.
        await owner_conn.execute(
            "UPDATE vendor SET fetch_policy = 'capture_only' WHERE name = 'Fennel Row'"
        )
        run_alembic("downgrade", "0033")
        policies = await _policies(owner_conn)
        assert policies["Larkspur Grocer"] == ("capture_only", None)
        assert policies["Willow Larder"] == ("capture_only", None)
        assert policies["Fennel Row"][0] == "capture_only"
        column = await owner_conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'vendor' AND column_name = 'refresh_paused_until'"
        )
        assert column == 0
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()
