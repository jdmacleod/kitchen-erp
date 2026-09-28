"""A recommit that fails part-way leaves the price book as it was.

Regression: ISSUE-001, found by /qa on 2026-09-28. Voiding an observation
committed on its own, so an edit that failed after the void (while emitting
the replacement, or removing a line) left the old price voided and no new
one: the purchase still read "committed" but had vanished from the price book.
"""

from datetime import UTC, datetime

import pytest

from app.services import purchases as purchases_service
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


async def test_failure_while_re_emitting_rolls_back_the_void(admin_client, monkeypatch):
    p, body = await _two_line_purchase(admin_client)
    before = await _live_ids(admin_client)
    assert len(before) == 2

    async def boom(*args, **kwargs):
        raise RuntimeError("emit failed")

    monkeypatch.setattr(purchases_service, "_emit", boom)
    body["lines"][0]["unit_price"] = "3.50"
    with pytest.raises(RuntimeError):
        await admin_client.put(f"/api/v1/purchases/{p['id']}", json=body)

    assert await _live_ids(admin_client) == before
    again = (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()
    assert again["lines"][0]["unit_price"] == "3.0000"


async def test_failure_on_a_later_line_rolls_back_earlier_replacements(admin_client, monkeypatch):
    # Review of #76: observe() committed too, so the first replacement made the
    # voids of every changed line permanent before the second one failed.
    p, body = await _two_line_purchase(admin_client)
    before = await _live_ids(admin_client)
    real_emit = purchases_service._emit
    calls = 0

    async def second_fails(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("emit failed")
        await real_emit(*args, **kwargs)

    monkeypatch.setattr(purchases_service, "_emit", second_fails)
    body["lines"][0]["unit_price"] = "3.50"
    body["lines"][1]["unit_price"] = "2.50"
    with pytest.raises(RuntimeError):
        await admin_client.put(f"/api/v1/purchases/{p['id']}", json=body)

    assert calls == 2
    assert await _live_ids(admin_client) == before


async def test_shelf_price_endpoint_still_commits(admin_client):
    p, _ = await _two_line_purchase(admin_client)
    line = p["lines"][0]
    r = await admin_client.post(
        "/api/v1/price-observations",
        json={
            "product_id": line["product"]["id"],
            "vendor_location_id": p["vendor_location"]["id"],
            "price": "3.25",
            "qty": "1",
            "unit": "lb",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["id"] in await _live_ids(admin_client)


async def test_void_endpoint_still_commits(admin_client):
    p, _ = await _two_line_purchase(admin_client)
    obs_id = p["lines"][0]["observation_id"]
    r = await admin_client.post(
        f"/api/v1/price-observations/{obs_id}/void", json={"reason": "typo"}
    )
    assert r.status_code == 200 and r.json()["voided"] is True
    assert obs_id not in await _live_ids(admin_client)
