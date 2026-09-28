"""An observation says which purchase it came from (#73).

The product page lists every price with a way to correct it: a shelf price is
voided where it is, and a price that came from a purchase is corrected in that
purchase, which needs the purchase's id.
"""

from datetime import UTC, datetime

from tests.pricebook_helpers import make_location, make_product


async def test_purchase_prices_name_their_purchase_and_shelf_prices_none(admin_client):
    loc = await make_location(admin_client, "Harbor Stall", "Harbor Stall", kind="stand")
    figs = await make_product(admin_client, "Figs", "Stall figs")
    r = await admin_client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": loc["id"],
            "purchased_at": datetime(2026, 5, 2, 16, 0, tzinfo=UTC).isoformat(),
            "lines": [{"product_id": figs["id"], "qty": "1", "unit": "lb", "unit_price": "3.00"}],
        },
    )
    assert r.status_code == 201, r.text
    purchase = r.json()
    r = await admin_client.post(
        "/api/v1/price-observations",
        json={
            "product_id": figs["id"],
            "vendor_location_id": loc["id"],
            "price": "2.75",
            "unit": "lb",
        },
    )
    assert r.status_code == 201, r.text
    shelf = r.json()
    assert shelf["purchase_id"] is None

    items = (
        await admin_client.get("/api/v1/price-observations", params={"product_id": figs["id"]})
    ).json()["items"]
    by_source = {o["source"]: o for o in items}
    assert by_source["manual"]["purchase_id"] == purchase["id"]
    assert by_source["shelf"]["purchase_id"] is None

    r = await admin_client.post(
        f"/api/v1/price-observations/{shelf['id']}/void", json={"reason": "wrong shelf"}
    )
    assert r.status_code == 200
    voided = (
        await admin_client.get(
            "/api/v1/price-observations",
            params={"product_id": figs["id"], "include_voided": "true"},
        )
    ).json()["items"]
    gone = next(o for o in voided if o["id"] == shelf["id"])
    assert gone["voided"] is True and gone["void_reason"] == "wrong shelf"
