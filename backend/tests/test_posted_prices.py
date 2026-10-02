"""Criterion 79 (04, 2L; plan PV1): posted web prices stay out of the default views.

A listing observation newer than a paid one changes no default view, no outlier
result and no narrowing; with "Include posted prices" on, it appears.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from app.core.db import get_sessionmaker
from app.models import AppUser, VendorListing
from app.services import pricebook, resolution
from tests.pricebook_helpers import make_location, make_product, shelf


async def post_listing_price(
    location: dict, product: dict, price: str, *, when: datetime | None = None
) -> str:
    """Record a posted price the way an accepted proposal will (2L), through observe()."""
    async with get_sessionmaker()() as db:
        user = (await db.execute(AppUser.__table__.select().limit(1))).first()
        listing = VendorListing(
            vendor_id=uuid.UUID(location["vendor"]["id"]),
            product_id=uuid.UUID(product["id"]),
            canonical_url=f"https://shop.example.test/p/{uuid.uuid4().hex[:8]}",
            title=product["name"],
            last_captured_at=datetime.now(UTC),
        )
        db.add(listing)
        await db.flush()
        obs = await pricebook.observe(
            db,
            product_id=uuid.UUID(product["id"]),
            vendor_location_id=uuid.UUID(location["id"]),
            price=Decimal(price),
            qty=Decimal("1"),
            unit="each",
            source="listing",
            listing_id=listing.id,
            entered_by=user.id,
            observed_at=when or datetime.now(UTC),
        )
        await db.commit()
        return str(obs.id)


@pytest.fixture
async def setup(admin_client):
    loc = await make_location(admin_client, "Hilltop Grocer", "Hilltop Grocer")
    tin = await make_product(
        admin_client, "Chickpeas", "Chickpea tin", pack_qty="400", pack_unit="g"
    )
    paid = await shelf(
        admin_client,
        tin["id"],
        loc["id"],
        "1.20",
        observed_at=(datetime.now(UTC) - timedelta(days=3)).isoformat(),
    )
    posted = await post_listing_price(loc, tin, "0.40")
    return loc, tin, paid, posted


async def test_default_views_leave_posted_prices_out(admin_client, setup):
    loc, tin, paid, posted = setup
    r = await admin_client.get(f"/api/v1/products/{tin['id']}/prices")
    body = r.json()
    assert [p["observation_id"] for p in body["points"]] == [paid["id"]]
    assert [p["observation_id"] for p in body["latest"]] == [paid["id"]]

    r = await admin_client.get(f"/api/v1/products/{tin['id']}/prices?include_posted=true")
    body = r.json()
    assert {p["observation_id"] for p in body["points"]} == {paid["id"], posted}
    assert [p["observation_id"] for p in body["latest"]] == [posted]  # newer, so latest


async def test_offers_compare_cheapest_and_history_leave_them_out(admin_client, setup):
    loc, tin, paid, posted = setup
    iid = tin["ingredient"]["id"]
    for suffix, expected in (("", paid["id"]), ("?include_posted=true", posted)):
        offers = (await admin_client.get(f"/api/v1/ingredients/{iid}/offers{suffix}")).json()
        assert [o["observation_id"] for o in offers["items"]] == [expected]
        cheapest = (
            await admin_client.get(
                f"/api/v1/price-book/cheapest?ingredient_id={iid}{suffix.replace('?', '&')}"
            )
        ).json()
        assert [c["observation_id"] for c in cheapest["items"]] == [expected]
    for include, expected in ((False, paid["id"]), (True, posted)):
        compare = (
            await admin_client.post(
                "/api/v1/price-book/compare",
                json={"ingredient_ids": [iid], "include_posted": include},
            )
        ).json()
        [cell] = compare["rows"][0]["cells"].values()
        assert cell["observation_id"] == expected
    history = (await admin_client.get(f"/api/v1/ingredients/{iid}/price-history")).json()
    assert {p["observation_id"] for p in history["points"]} == {paid["id"]}
    panel = (await admin_client.get(f"/api/v1/vendor-locations/{loc['id']}/price-panel")).json()
    assert [r["observation_id"] for r in panel["recent"]] == [paid["id"]]


async def test_outlier_and_narrowing_ignore_posted_prices(admin_client, setup, db_session):
    """Both read the recent median: a posted price at a third of the paid one must not move it."""
    loc, tin, paid, posted = setup
    median = await resolution.recent_median_unit_price(db_session, uuid.UUID(tin["id"]))
    assert median is not None
    # The paid price alone: 1.20 for 400 g.
    assert median == pricebook.unit_price(Decimal("1.20"), Decimal("400"))


async def test_the_listing_link_is_required_exactly_for_posted_prices(
    admin_client, setup, owner_conn: asyncpg.Connection
):
    loc, tin, paid, posted = setup
    listing = await owner_conn.fetchval(
        "SELECT listing_id FROM price_observation WHERE id = $1", uuid.UUID(posted)
    )
    assert listing is not None
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "INSERT INTO price_observation (id, product_id, vendor_location_id, observed_at, "
            "price, qty, unit, is_promo, source, entered_by) "
            "SELECT gen_random_uuid(), product_id, vendor_location_id, now(), 1, 1, 'each', "
            "false, 'listing', entered_by FROM price_observation WHERE id = $1",
            uuid.UUID(paid["id"]),
        )


async def test_observations_say_where_a_posted_price_came_from(admin_client, setup):
    loc, tin, paid, posted = setup
    r = await admin_client.get(f"/api/v1/price-observations/{posted}")
    assert r.status_code == 200
    assert r.json()["source"] == "listing" and r.json()["listing_id"] is not None


async def test_the_runtime_role_reads_both_chains(app_conn: asyncpg.Connection):
    for view in (
        "price_current_all",
        "offer_applicable_all",
        "offer_latest_all",
        "offer_latest_regular_all",
        "ingredient_offer_all",
        "ingredient_offer",
    ):
        await app_conn.fetch(f"SELECT * FROM {view} LIMIT 1")
