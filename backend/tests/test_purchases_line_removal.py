"""Removing a line that is already in the price book is refused, not a 500.

Regression: ISSUE-002, found by /qa on 2026-09-28. Deleting such a line broke
the append-only observation's reference to it (an IntegrityError, shown as
"Internal server error"). The line can be ignored instead; a line that never
reached the price book can still be deleted.
"""

from datetime import UTC, datetime

from tests.pricebook_helpers import make_location, make_product


async def _two_line_purchase(client):
    loc = await make_location(client, "Harbor Stall", "Harbor Stall", kind="stand")
    figs = await make_product(client, "Figs", "Stall figs")
    plums = await make_product(client, "Plums", "Stall plums")
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime(2026, 5, 2, 16, 0, tzinfo=UTC).isoformat(),
        "lines": [
            {"product_id": figs["id"], "qty": "2", "unit": "lb", "unit_price": "3.00"},
            {"product_id": plums["id"], "qty": "1", "unit": "lb", "unit_price": "2.00"},
        ],
    }
    r = await client.post("/api/v1/purchases", json=body)
    assert r.status_code == 201, r.text
    return r.json(), body


async def _live_ids(client) -> set[str]:
    items = (await client.get("/api/v1/price-observations")).json()["items"]
    return {o["id"] for o in items}


async def test_edit_that_drops_a_recorded_line_is_409_and_changes_nothing(admin_client):
    p, body = await _two_line_purchase(admin_client)
    before = await _live_ids(admin_client)
    body["lines"] = [{**body["lines"][0], "qty": "3"}]
    r = await admin_client.put(f"/api/v1/purchases/{p['id']}", json=body)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "line_recorded"
    assert await _live_ids(admin_client) == before
    again = (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()
    assert [ln["qty"] for ln in again["lines"]] == ["2", "1"]


async def test_review_delete_of_a_recorded_line_is_409(admin_client):
    p, _ = await _two_line_purchase(admin_client)
    r = await admin_client.post(f"/api/v1/purchases/{p['id']}/reopen")
    assert r.status_code == 200, r.text
    line_id = p["lines"][1]["id"]
    r = await admin_client.delete(f"/api/v1/purchases/{p['id']}/lines/{line_id}")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "line_recorded"
    # A line added in review never reached the price book and still deletes.
    r = await admin_client.post(
        f"/api/v1/purchases/{p['id']}/lines", json={"line_kind": "item", "line_total": "1.00"}
    )
    assert r.status_code == 201, r.text
    added = next(ln for ln in r.json()["lines"] if ln["seq"] == 3)
    r = await admin_client.delete(f"/api/v1/purchases/{p['id']}/lines/{added['id']}")
    assert r.status_code == 200, r.text
    assert [ln["seq"] for ln in r.json()["lines"]] == [1, 2]
