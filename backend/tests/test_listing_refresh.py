"""Scheduled listing refreshes for the products helper (04, 2N; decided 2026-10-02).

Vendors, pages and products are invented.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.services import lookups
from tests.pricebook_helpers import make_location, make_product
from tests.test_products_helper import helper  # noqa: F401  the stub helper's client
from tests.test_scopes import token

PAGE = "https://shop.example.test/p/rolled-oats-500"


async def listing(
    owner_conn: asyncpg.Connection,
    vendor_id: str,
    product_id: str,
    url: str,
    status: str = "active",
) -> uuid.UUID:
    listing_id = uuid.uuid4()
    await owner_conn.execute(
        "INSERT INTO vendor_listing (id, vendor_id, product_id, canonical_url, title, "
        "last_captured_at, status) VALUES ($1, $2, $3, $4, 'Oats', now(), $5)",
        listing_id,
        uuid.UUID(vendor_id),
        uuid.UUID(product_id),
        url,
        status,
    )
    return listing_id


async def queue(now: datetime | None = None) -> int:
    async with get_sessionmaker()() as db:
        return await lookups.queue_listing_refreshes(db, now)


@pytest.fixture
async def listings(admin_client, owner_conn):
    store = await make_location(admin_client, "Juniper Market", "Juniper Market")
    oats = await make_product(admin_client, "Oats", "Rolled oats tin")
    active = await listing(owner_conn, store["vendor"]["id"], oats["id"], PAGE)
    await listing(owner_conn, store["vendor"]["id"], oats["id"], f"{PAGE}-old", status="gone")
    return active, oats


async def test_nothing_is_queued_without_a_helper(listings):
    assert await queue() == 0


async def test_each_active_listing_is_queued_once_with_its_id(
    admin_client,
    listings,
    helper,  # noqa: F811
):
    active, oats = listings
    assert await queue() == 1
    assert await queue() == 0  # already open
    [request] = (await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)).json()[
        "items"
    ]
    assert (request["kind"], request["value"], request["listing_id"]) == ("page", PAGE, str(active))


async def test_a_listing_is_queued_again_after_the_interval(
    admin_client,
    listings,
    owner_conn,
    helper,  # noqa: F811
):
    assert await queue() == 1
    await owner_conn.execute("UPDATE lookup_request SET status = 'answered'")
    assert await queue() == 0  # answered within the interval
    later = datetime.now(UTC) + timedelta(days=get_settings().listing_refresh_days, hours=1)
    assert await queue(later) == 1


async def test_zero_days_turns_it_off(admin_client, listings, monkeypatch):
    await token(admin_client, "products:read")
    monkeypatch.setattr(get_settings(), "listing_refresh_days", 0)
    assert await queue() == 0


async def test_only_page_requests_name_a_listing(listings, owner_conn):
    active, oats = listings
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "INSERT INTO lookup_request (id, kind, product_id, listing_id, value) "
            "VALUES (gen_random_uuid(), 'gtin', $1, $2, '00012345678905')",
            uuid.UUID(oats["id"]),
            active,
        )
