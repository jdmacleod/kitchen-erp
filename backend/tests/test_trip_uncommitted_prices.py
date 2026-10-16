"""An uncommitted purchase's price never fills a trip item (17, 4B): criterion 15.

The planner's offers come from committed purchases and shelf prices only, the
same restriction recipe costing uses (`trip_offers`). Here a cheaper price
from a purchase reopened for review sits at a nearer store, and the plan
neither buys there nor counts it. Stores, products and figures are invented;
the geography is synthetic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from tests import geo_helpers as gh
from tests.pricebook_helpers import make_product, shelf
from tests.test_trip_planner import add_item, kitchen, plan_trip, shops, variant  # noqa: F401

clean_geo = gh.clean_geo
no_network = gh.no_network


async def test_criterion_15_an_uncommitted_purchases_price_never_fills_an_item(
    admin_client: httpx.AsyncClient,
    kitchen: dict,  # noqa: F811
    shops: dict,  # noqa: F811
    no_network: None,
):
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    # The only committed price: the stall's, at 3.00.
    await shelf(admin_client, barley["id"], shops["stall"]["id"], "3.00")
    # A purchase at the chain's north store for 1.50, then reopened: uncommitted.
    r = await admin_client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": shops["north"]["id"],
            "purchased_at": datetime.now(UTC).isoformat(),
            "lines": [
                {"product_id": barley["id"], "qty": "1", "unit": "each", "line_total": "1.50"}
            ],
        },
        headers={"Idempotency-Key": "harbor-trip-draft-1"},
    )
    assert r.status_code == 201, r.text
    purchase_id = r.json()["id"]
    reopened = await admin_client.post(f"/api/v1/purchases/{purchase_id}/reopen")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "reviewed"  # uncommitted
    await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1", "unit": "kg"}],
    )

    plan = await plan_trip(admin_client, kitchen, max_stops=1)
    for v in plan["variants"]:
        (stop,) = v["stops"]
        assert stop["vendor_location_id"] == shops["stall"]["id"], v["kind"]
        (line,) = stop["items"]
        assert line["line_cost"] == "3.0000" and line["product_id"] == barley["id"], v["kind"]
    # Nothing in any variant rests on the uncommitted figure or store.
    for v in plan["variants"]:
        assert all(s["vendor_location_id"] != shops["north"]["id"] for s in v["stops"])
        assert not any("1.50" in a.get("text", "") for a in v["annotations"])

    # Committed again, the same price fills the item at the nearer store.
    committed = await admin_client.post(f"/api/v1/purchases/{purchase_id}/commit")
    assert committed.status_code == 200, committed.text
    plan = await plan_trip(admin_client, kitchen, max_stops=1)
    cheapest = variant(plan, "cheapest")
    assert cheapest["stops"][0]["vendor_location_id"] in {
        shops["north"]["id"],
        shops["south"]["id"],
    }
    assert cheapest["stops"][0]["items"][0]["line_cost"] == "1.5000"
