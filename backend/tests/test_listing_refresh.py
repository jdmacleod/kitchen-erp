"""Scheduled listing refreshes for the products helper (04, 2N; decided 2026-10-02).

Refreshes follow ``vendor.fetch_policy`` and pause while a store's pages keep
coming back unreachable (#264). Vendors, pages and products are invented.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.schemas.products_interchange import FORMAT
from app.services import lookups
from tests.pricebook_helpers import make_location, make_product
from tests.test_products_helper import (
    helper,  # noqa: F401  the stub helper's client
    post_answer,
)
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


async def allow_fetch(admin_client, vendor_id: str, policy: str = "server_fetch") -> dict:
    r = await admin_client.patch(f"/api/v1/vendors/{vendor_id}", json={"fetch_policy": policy})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
async def listings(admin_client, owner_conn):
    store = await make_location(admin_client, "Juniper Market", "Juniper Market")
    await allow_fetch(admin_client, store["vendor"]["id"])
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


# --- #264: fetch_policy and the pause -------------------------------------------------


@pytest.fixture
async def store_pages(admin_client, owner_conn):
    """A store the helper may fetch, with four listings: enough for a pause in one round."""
    store = await make_location(admin_client, "Larkspur Grocer", "Larkspur Grocer")
    vendor_id = store["vendor"]["id"]
    await allow_fetch(admin_client, vendor_id)
    product = await make_product(admin_client, "Barley", "Pearl barley bag")
    for n in range(4):
        await listing(owner_conn, vendor_id, product["id"], f"https://larkspur.example.test/p/{n}")
    return vendor_id


async def open_page_requests(helper) -> list[dict]:  # noqa: F811
    items = (await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)).json()[
        "items"
    ]
    return [r for r in items if r["kind"] == "page"]


def nothing_found(request_id: str, **extra) -> str:
    return json.dumps({"format": FORMAT, "request_id": request_id, "found": False, **extra})


async def vendor(admin_client, vendor_id: str) -> dict:
    return (await admin_client.get(f"/api/v1/vendors/{vendor_id}")).json()


async def test_a_capture_only_vendor_gets_no_refresh(
    admin_client,
    owner_conn,
    helper,  # noqa: F811
):
    store = await make_location(admin_client, "Willow Larder", "Willow Larder")
    assert (await vendor(admin_client, store["vendor"]["id"]))["fetch_policy"] == "capture_only"
    product = await make_product(admin_client, "Millet", "Millet bag")
    await listing(owner_conn, store["vendor"]["id"], product["id"], f"{PAGE}-millet")
    assert await queue() == 0
    await allow_fetch(admin_client, store["vendor"]["id"])
    assert await queue() == 1


async def test_repeated_unreachable_answers_pause_the_store(
    admin_client,
    store_pages,
    monkeypatch,
    helper,  # noqa: F811
):
    monkeypatch.setattr(get_settings(), "listing_refresh_days", 2)
    assert await queue() == 4
    requests = await open_page_requests(helper)
    for r in requests[:2]:
        assert (await post_answer(helper, nothing_found(r["id"], reason="unreachable"))).json()[
            "outcome"
        ] == "no_change"
    shown = await vendor(admin_client, store_pages)
    assert shown["refresh_paused_until"] is None  # two in a row: not yet
    assert shown["refresh_unreachable_since"] is not None
    await post_answer(helper, nothing_found(requests[2]["id"], reason="unreachable"))
    paused = await vendor(admin_client, store_pages)
    until = datetime.fromisoformat(paused["refresh_paused_until"])
    assert timedelta(days=6) < until - datetime.now(UTC) <= timedelta(days=7)
    # A fourth failure while paused doesn't stretch the pause.
    await post_answer(helper, nothing_found(requests[3]["id"], reason="unreachable"))
    assert (await vendor(admin_client, store_pages))["refresh_paused_until"] == paused[
        "refresh_paused_until"
    ]
    # Paused: nothing is queued, even once the listings are due again.
    later = datetime.now(UTC) + timedelta(days=2, hours=1)
    assert await queue(later) == 0
    # After the pause, they're queued again.
    assert await queue(until + timedelta(hours=1)) == 4


async def test_the_pause_doubles_when_it_ends_in_another_failure(
    admin_client,
    store_pages,
    owner_conn,
    helper,  # noqa: F811
):
    await owner_conn.execute(
        "UPDATE vendor SET refresh_failures = 3, refresh_backoff_days = 7, "
        "refresh_unreachable_since = now() - interval '8 days', "
        "refresh_paused_until = now() - interval '1 hour' WHERE id = $1",
        uuid.UUID(store_pages),
    )
    assert await queue() == 4
    [first, *_] = await open_page_requests(helper)
    await post_answer(helper, nothing_found(first["id"], reason="unreachable"))
    shown = await vendor(admin_client, store_pages)
    until = datetime.fromisoformat(shown["refresh_paused_until"])
    assert timedelta(days=13) < until - datetime.now(UTC) <= timedelta(days=14)


async def test_a_page_that_was_read_clears_the_pause(
    admin_client,
    store_pages,
    helper,  # noqa: F811
):
    assert await queue() == 4
    requests = await open_page_requests(helper)
    for r in requests[:3]:
        await post_answer(helper, nothing_found(r["id"], reason="unreachable"))
    assert (await vendor(admin_client, store_pages))["refresh_paused_until"] is not None
    read = json.dumps({"format": FORMAT, "request_id": requests[3]["id"], "found": True})
    assert (await post_answer(helper, read)).status_code == 200
    shown = await vendor(admin_client, store_pages)
    assert (shown["refresh_paused_until"], shown["refresh_unreachable_since"]) == (None, None)


async def test_nothing_found_without_a_reason_never_pauses(
    admin_client,
    store_pages,
    owner_conn,
    helper,  # noqa: F811
):
    # An older helper, or a page that loaded but held nothing to read.
    assert await queue() == 4
    for r in await open_page_requests(helper):
        await post_answer(helper, nothing_found(r["id"]))
    shown = await vendor(admin_client, store_pages)
    assert (shown["refresh_paused_until"], shown["refresh_unreachable_since"]) == (None, None)
    details = await owner_conn.fetch("SELECT detail FROM lookup_answer")
    assert {d["detail"] for d in details} == {None}


async def test_an_unreachable_answer_is_recorded_as_such(
    store_pages,
    owner_conn,
    helper,  # noqa: F811
):
    assert await queue() == 4
    [first, *_] = await open_page_requests(helper)
    await post_answer(helper, nothing_found(first["id"], reason="unreachable"))
    row = await owner_conn.fetchrow("SELECT outcome, detail FROM lookup_answer")
    assert (row["outcome"], row["detail"]) == ("no_change", "unreachable")


async def test_an_unknown_reason_is_refused(
    store_pages,
    helper,  # noqa: F811
):
    assert await queue() == 4
    [first, *_] = await open_page_requests(helper)
    r = await post_answer(helper, nothing_found(first["id"], reason="tired"))
    assert r.status_code == 422


async def test_check_now_ends_the_pause_and_queues_the_listings(
    admin_client,
    store_pages,
    owner_conn,
    helper,  # noqa: F811
):
    await owner_conn.execute(
        "UPDATE vendor SET refresh_failures = 3, refresh_backoff_days = 7, "
        "refresh_unreachable_since = now(), refresh_paused_until = now() + interval '7 days' "
        "WHERE id = $1",
        uuid.UUID(store_pages),
    )
    r = await admin_client.post(f"/api/v1/vendors/{store_pages}/check-prices")
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 4
    assert r.json()["vendor"]["refresh_paused_until"] is None
    assert len(await open_page_requests(helper)) == 4
    # Asked again while they're open: nothing doubles.
    assert (await admin_client.post(f"/api/v1/vendors/{store_pages}/check-prices")).json()[
        "queued"
    ] == 0


async def test_check_now_needs_fetching_allowed_and_a_helper(admin_client, owner_conn):
    store = await make_location(admin_client, "Fennel Row", "Fennel Row")
    vendor_id = store["vendor"]["id"]
    r = await admin_client.post(f"/api/v1/vendors/{vendor_id}/check-prices")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "fetch_not_allowed")
    await allow_fetch(admin_client, vendor_id)
    r = await admin_client.post(f"/api/v1/vendors/{vendor_id}/check-prices")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "no_helper")


async def test_turning_fetching_off_clears_the_pause(admin_client, store_pages, owner_conn):
    await owner_conn.execute(
        "UPDATE vendor SET refresh_failures = 3, refresh_backoff_days = 7, "
        "refresh_unreachable_since = now(), refresh_paused_until = now() + interval '7 days' "
        "WHERE id = $1",
        uuid.UUID(store_pages),
    )
    shown = await allow_fetch(admin_client, store_pages, "capture_only")
    assert shown["fetch_policy"] == "capture_only"
    assert (shown["refresh_paused_until"], shown["refresh_unreachable_since"]) == (None, None)


# --- refreshes already queued when fetching stops (#264) -------------------------------


async def _statuses(owner_conn: asyncpg.Connection) -> dict[str, int]:
    rows = await owner_conn.fetch(
        "SELECT status, count(*) AS n FROM lookup_request "
        "WHERE kind = 'page' AND listing_id IS NOT NULL GROUP BY status"
    )
    return {r["status"]: r["n"] for r in rows}


async def _product_page_request(owner_conn: asyncpg.Connection, product_id: str) -> uuid.UUID:
    """A page request for a product, not a listing refresh: never closed by these rules."""
    request_id = uuid.uuid4()
    await owner_conn.execute(
        "INSERT INTO lookup_request (id, kind, product_id, value) "
        "VALUES ($1, 'page', $2, 'https://larkspur.example.test/p/other')",
        request_id,
        uuid.UUID(product_id),
    )
    return request_id


async def test_queued_refreshes_are_held_back_once_fetching_stops(
    admin_client,
    store_pages,
    owner_conn,
    helper,  # noqa: F811
):
    assert await queue() == 4
    assert len(await open_page_requests(helper)) == 4
    # Changed behind the app's back: the helper still isn't sent there.
    await owner_conn.execute(
        "UPDATE vendor SET fetch_policy = 'capture_only' WHERE id = $1", uuid.UUID(store_pages)
    )
    assert await open_page_requests(helper) == []
    await owner_conn.execute(
        "UPDATE vendor SET fetch_policy = 'server_fetch', "
        "refresh_paused_until = now() + interval '7 days' WHERE id = $1",
        uuid.UUID(store_pages),
    )
    assert await open_page_requests(helper) == []
    await owner_conn.execute(
        "UPDATE vendor SET refresh_paused_until = now() - interval '1 hour' WHERE id = $1",
        uuid.UUID(store_pages),
    )
    assert len(await open_page_requests(helper)) == 4


async def test_turning_fetching_off_closes_queued_refreshes(
    admin_client,
    store_pages,
    owner_conn,
    helper,  # noqa: F811
):
    assert await queue() == 4
    product_id = await owner_conn.fetchval(
        "SELECT product_id FROM vendor_listing WHERE vendor_id = $1 LIMIT 1",
        uuid.UUID(store_pages),
    )
    other = await _product_page_request(owner_conn, str(product_id))
    await allow_fetch(admin_client, store_pages, "capture_only")
    assert await _statuses(owner_conn) == {"closed": 4}
    status = await owner_conn.fetchval("SELECT status FROM lookup_request WHERE id = $1", other)
    assert status == "open"
    assert [r["id"] for r in await open_page_requests(helper)] == [str(other)]


async def test_a_pause_closes_the_rest_of_the_queued_refreshes(
    admin_client,
    store_pages,
    owner_conn,
    helper,  # noqa: F811
):
    assert await queue() == 4
    requests = await open_page_requests(helper)
    for r in requests[:3]:
        await post_answer(helper, nothing_found(r["id"], reason="unreachable"))
    assert (await vendor(admin_client, store_pages))["refresh_paused_until"] is not None
    assert await _statuses(owner_conn) == {"answered": 3, "closed": 1}
    assert await open_page_requests(helper) == []
