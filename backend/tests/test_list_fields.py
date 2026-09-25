"""List fields the Products table and Vendor cards show (T16): last_paid on products, and
location_count and last_visit on vendors. Both count committed purchases only."""

from datetime import UTC, datetime

import httpx

from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf


async def _buy(client: httpx.AsyncClient, location_id: str, product_id: str, total: str, when):
    r = await client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": location_id,
            "purchased_at": when.isoformat(),
            "lines": [{"product_id": product_id, "qty": "2", "unit": "each", "line_total": total}],
        },
        headers={"Idempotency-Key": f"buy-{location_id}-{total}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _listed_product(client: httpx.AsyncClient, product_id: str) -> dict:
    items = (await client.get("/api/v1/products")).json()["items"]
    return next(p for p in items if p["id"] == product_id)


async def test_last_paid_is_the_latest_committed_purchase(admin_client):
    pier = await make_location(admin_client, "Pier Stand", "Pier Stand", kind="stand")
    hill = await make_location(admin_client, "Hill Market", "Hill Market", coords=SYNTH[1])
    figs = await make_product(admin_client, "Figs", "Stand figs", canonical_unit="each")
    other = await make_product(admin_client, "Plums", "Stand plums", canonical_unit="each")

    assert (await _listed_product(admin_client, figs["id"]))["last_paid"] is None

    await _buy(admin_client, pier["id"], figs["id"], "5.00", datetime(2026, 5, 1, tzinfo=UTC))
    later = await _buy(
        admin_client, hill["id"], figs["id"], "6.50", datetime(2026, 6, 1, tzinfo=UTC)
    )
    # A shelf price is newer, but nothing was paid.
    await shelf(admin_client, figs["id"], pier["id"], "1.00")

    paid = (await _listed_product(admin_client, figs["id"]))["last_paid"]
    assert paid == {
        "price": "6.5000",
        "qty": "2",
        "unit": "each",
        "is_promo": False,
        "vendor_id": hill["vendor"]["id"],
        "vendor_name": "Hill Market",
        "purchase_id": later["id"],
        "paid_at": "2026-06-01T00:00:00Z",
    }
    assert (await _listed_product(admin_client, other["id"]))["last_paid"] is None

    # Reopening leaves the observation live, but the purchase is no longer committed.
    r = await admin_client.post(f"/api/v1/purchases/{later['id']}/reopen")
    assert r.status_code == 200, r.text
    paid = (await _listed_product(admin_client, figs["id"]))["last_paid"]
    assert paid["vendor_name"] == "Pier Stand" and paid["price"] == "5.0000"


async def test_vendor_cards_count_active_locations_and_the_last_committed_visit(admin_client):
    first = await make_location(admin_client, "Tide Grocers", "Tide Grocers north", kind="chain")
    vendor_id = first["vendor"]["id"]
    second = await make_location(
        admin_client, "", "Tide Grocers south", vendor_id=vendor_id, coords=SYNTH[1]
    )
    closed = await make_location(
        admin_client, "", "Tide Grocers east", vendor_id=vendor_id, coords=SYNTH[2]
    )
    r = await admin_client.post(f"/api/v1/vendor-locations/{closed['id']}/deactivate")
    assert r.status_code == 200, r.text
    await make_location(admin_client, "Quiet Stand", "Quiet Stand", kind="stand")
    melon = await make_product(admin_client, "Melon", "Melon", canonical_unit="each")

    await _buy(admin_client, first["id"], melon["id"], "3.00", datetime(2026, 4, 2, tzinfo=UTC))
    reopened = await _buy(
        admin_client, second["id"], melon["id"], "4.00", datetime(2026, 7, 9, tzinfo=UTC)
    )
    r = await admin_client.post(f"/api/v1/purchases/{reopened['id']}/reopen")
    assert r.status_code == 200, r.text

    items = {v["name"]: v for v in (await admin_client.get("/api/v1/vendors")).json()["items"]}
    assert items["Tide Grocers"]["location_count"] == 2
    assert items["Tide Grocers"]["last_visit"] == "2026-04-02T00:00:00Z"
    assert items["Quiet Stand"]["location_count"] == 1
    assert items["Quiet Stand"]["last_visit"] is None


async def test_purchase_lines_carry_their_ingredient_category(admin_client):
    pier = await make_location(admin_client, "Pier Stand", "Pier Stand", kind="stand")
    r = await admin_client.post(
        "/api/v1/products",
        json={
            "ingredient": {"name": "Pears", "canonical_unit": "each", "category": "Fruit"},
            "name": "Stand pears",
        },
    )
    assert r.status_code == 201, r.text
    bought = await _buy(
        admin_client, pier["id"], r.json()["id"], "3.00", datetime(2026, 5, 1, tzinfo=UTC)
    )
    [line] = (await admin_client.get(f"/api/v1/purchases/{bought['id']}")).json()["lines"]
    assert (line["product"]["category"], line["product"]["category_key"]) == ("Fruit", "produce")
