from datetime import UTC, datetime
from decimal import Decimal

import httpx

from tests.pricebook_helpers import make_location, make_product

D = Decimal


async def setup(client: httpx.AsyncClient):
    loc = await make_location(client, "Pier Stand", "Pier Stand", kind="stand")
    berries = await make_product(client, "Strawberries", "Stand strawberries")
    peaches = await make_product(client, "Peaches", "Stand peaches")
    r = await client.post(
        "/api/v1/products",
        json={
            "ingredient": {"name": "Eggs", "canonical_unit": "each"},
            "name": "Dozen eggs",
            "pack_qty": "12",
            "pack_unit": "each",
        },
    )
    assert r.status_code == 201, r.text
    eggs = r.json()
    return loc, berries, peaches, eggs


async def test_three_line_purchase_commits_and_emits_one_observation_per_line(admin_client):
    loc, berries, peaches, eggs = await setup(admin_client)
    when = datetime(2026, 6, 6, 17, 0, tzinfo=UTC).isoformat()
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": when,
        "lines": [
            {"product_id": berries["id"], "qty": "2", "unit": "lb", "unit_price": "4.50"},
            {"product_id": peaches["id"], "qty": "1.5", "unit": "lb", "line_total": "5.10"},
            {"product_id": eggs["id"], "qty": "1", "unit": "each", "unit_price": "6"},
        ],
    }
    r = await admin_client.post("/api/v1/purchases", json=body, headers={"Idempotency-Key": "p1"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["status"] == "committed" and p["source"] == "manual"
    assert p["total"] == "20.1000" and p["computed_total"] == "20.1000" and p["flags"] == []
    lines = p["lines"]
    assert [ln["seq"] for ln in lines] == [1, 2, 3]
    assert lines[0]["line_total"] == "9.0000"  # 2 x 4.50 computed
    assert lines[1]["unit_price"] == "3.4000"  # 5.10 / 1.5 computed
    assert all(ln["observation_id"] for ln in lines)
    assert all(ln["resolution"] == "manual" for ln in lines)
    # Replay is idempotent.
    again = await admin_client.post(
        "/api/v1/purchases", json=body, headers={"Idempotency-Key": "p1"}
    )
    assert again.status_code == 201 and again.json()["id"] == p["id"]
    listed = (await admin_client.get("/api/v1/purchases")).json()["items"]
    assert [x["id"] for x in listed] == [p["id"]]
    # Observations are linked by line and dated to the purchase.
    obs = (await admin_client.get("/api/v1/price-observations")).json()["items"]
    assert len(obs) == 3
    by_line = {o["purchase_line_id"]: o for o in obs}
    egg_line = lines[2]
    egg_obs = by_line[egg_line["id"]]
    assert egg_obs["price"] == "6.0000" and egg_obs["qty"] == "1" and egg_obs["unit"] == "each"
    assert egg_obs["source"] == "manual" and egg_obs["observed_at"].startswith("2026-06-06T17:00")
    assert egg_obs["norm"]["status"] == "ok" and egg_obs["norm"]["canonical_qty"] == "12"
    assert egg_obs["norm"]["norm_unit_price"] == "0.500000"
    # The product remembers its last purchase unit.
    r = await admin_client.get(f"/api/v1/products/{berries['id']}/last-purchase-unit")
    assert r.json() == {"unit": "lb"}


async def test_computed_fields_are_decimal_and_rounded_half_even(admin_client):
    loc, berries, *_ = await setup(admin_client)
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime.now(UTC).isoformat(),
        "lines": [{"product_id": berries["id"], "qty": "2.31", "unit": "lb", "unit_price": "3.99"}],
    }
    p = (await admin_client.post("/api/v1/purchases", json=body)).json()
    assert p["lines"][0]["line_total"] == "9.2169"
    body["lines"] = [{"product_id": berries["id"], "qty": "3", "unit": "each", "line_total": "10"}]
    p = (await admin_client.post("/api/v1/purchases", json=body)).json()
    assert p["lines"][0]["unit_price"] == "3.3333"
    bad = {**body, "lines": [{"product_id": berries["id"], "qty": "1", "unit": "each"}]}
    posted = await admin_client.post("/api/v1/purchases", json=bad)
    assert posted.status_code == 422
    both = {
        **body,
        "lines": [
            {
                "product_id": berries["id"],
                "qty": "1",
                "unit": "each",
                "unit_price": "1",
                "line_total": "1",
            }
        ],
    }
    posted = await admin_client.post("/api/v1/purchases", json=both)
    assert posted.status_code == 422


async def test_entered_total_is_reconciled(admin_client):
    loc, berries, *_ = await setup(admin_client)
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime.now(UTC).isoformat(),
        "total": "12.00",
        "lines": [{"product_id": berries["id"], "qty": "2", "unit": "lb", "unit_price": "4.50"}],
    }
    p = (await admin_client.post("/api/v1/purchases", json=body)).json()
    assert p["total"] == "12.0000" and p["computed_total"] == "9.0000"
    assert p["flags"] == ["total_mismatch"]


async def test_reopen_voids_and_reemits_only_changed_lines(admin_client):
    loc, berries, peaches, eggs = await setup(admin_client)
    when = datetime.now(UTC).isoformat()
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": when,
        "lines": [
            {"product_id": berries["id"], "qty": "2", "unit": "lb", "unit_price": "4.50"},
            {"product_id": peaches["id"], "qty": "1", "unit": "lb", "unit_price": "3.00"},
        ],
    }
    p = (await admin_client.post("/api/v1/purchases", json=body)).json()
    first_obs = {ln["seq"]: ln["observation_id"] for ln in p["lines"]}
    # The edit form names each saved line by id (#72).
    for line, saved in zip(body["lines"], p["lines"], strict=True):
        line["id"] = saved["id"]
    body["lines"][1]["unit_price"] = "3.50"  # only the second line changes
    r = await admin_client.put(f"/api/v1/purchases/{p['id']}", json=body)
    assert r.status_code == 200, r.text
    q = r.json()
    second_obs = {ln["seq"]: ln["observation_id"] for ln in q["lines"]}
    assert second_obs[1] == first_obs[1], "untouched line keeps its observation"
    assert second_obs[2] != first_obs[2], "changed line gets a new observation"
    old = (await admin_client.get(f"/api/v1/price-observations/{first_obs[2]}")).json()
    assert old["voided"] is True and old["void_reason"] == "line edited on recommit"
    new = (await admin_client.get(f"/api/v1/price-observations/{second_obs[2]}")).json()
    assert new["price"] == "3.5000" and new["voided"] is False
    live = (await admin_client.get("/api/v1/price-observations")).json()["items"]
    assert len(live) == 2
    # Remove a line and add one: the removed line's observation is voided.
    body["lines"] = [
        body["lines"][0],
        {"product_id": eggs["id"], "qty": "1", "unit": "each", "unit_price": "6"},
    ]
    q = (await admin_client.put(f"/api/v1/purchases/{p['id']}", json=body)).json()
    assert [ln["product"]["id"] for ln in q["lines"]] == [berries["id"], eggs["id"]]
    gone = (await admin_client.get(f"/api/v1/price-observations/{second_obs[2]}")).json()
    assert gone["voided"] is True and gone["void_reason"] == "line removed"
    assert q["total"] == "15.0000"


async def test_unknown_product_or_location_is_404(admin_client):
    loc, berries, *_ = await setup(admin_client)
    import uuid

    body = {
        "vendor_location_id": str(uuid.uuid4()),
        "purchased_at": datetime.now(UTC).isoformat(),
        "lines": [{"product_id": berries["id"], "qty": "1", "unit": "each", "unit_price": "1"}],
    }
    posted = await admin_client.post("/api/v1/purchases", json=body)
    assert posted.status_code == 404
    body["vendor_location_id"] = loc["id"]
    body["lines"][0]["product_id"] = str(uuid.uuid4())
    posted = await admin_client.post("/api/v1/purchases", json=body)
    assert posted.status_code == 404


async def test_purchases_list_newest_bought_first_across_pages(admin_client):
    """Spec 10, Shop: purchases: newest first means by purchase date, not entry order.

    Old receipts entered after newer shopping (an import, a scanned backlog) must
    not jump ahead, and the order must hold across page boundaries.
    """
    loc, berries, _, _ = await setup(admin_client)
    entered = [(2026, 6, 6), (2026, 3, 1), (2026, 8, 20), (2026, 3, 1), (2025, 12, 31)]
    for i, (y, m, d) in enumerate(entered):
        body = {
            "vendor_location_id": loc["id"],
            "purchased_at": datetime(y, m, d, 17, 0, tzinfo=UTC).isoformat(),
            "lines": [{"product_id": berries["id"], "qty": "1", "unit": "lb", "line_total": "4"}],
        }
        r = await admin_client.post(
            "/api/v1/purchases", json=body, headers={"Idempotency-Key": f"o{i}"}
        )
        assert r.status_code == 201, r.text
    seen, cursor = [], None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = (await admin_client.get("/api/v1/purchases", params=params)).json()
        seen += [p["purchased_at"][:10] for p in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == ["2026-08-20", "2026-06-06", "2026-03-01", "2026-03-01", "2025-12-31"]
    r = await admin_client.get("/api/v1/purchases", params={"cursor": "bm90LWEtY3Vyc29y"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_cursor"


async def _bought(client, loc, product, when, total, key):
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime(*when, 17, 0, tzinfo=UTC).isoformat(),
        "lines": [{"product_id": product["id"], "qty": "1", "unit": "each", "line_total": total}],
    }
    r = await client.post("/api/v1/purchases", json=body, headers={"Idempotency-Key": key})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _all_pages(client, **params):
    seen, cursor = [], None
    while True:
        page = (
            await client.get(
                "/api/v1/purchases",
                params={"limit": 2, **params} | ({"cursor": cursor} if cursor else {}),
            )
        ).json()
        seen += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            return seen


async def test_purchases_find_and_sort(admin_client):
    """Spec 10, Shop: purchases, UI-3.22: find a store or item; sort by Date, Where, Total."""
    from tests.pricebook_helpers import SYNTH

    pier = await make_location(admin_client, "Pier Stand", "Pier Stand", kind="stand")
    larch = await make_location(admin_client, "Larch Market", "Larch Market East", coords=SYNTH[1])
    chile = await make_product(admin_client, "Jalapeño", "Jalapeño peppers")
    honey = await make_product(admin_client, "Honey", "Wildflower honey")
    a = await _bought(admin_client, pier, chile, (2026, 5, 1), "3.00", "f1")
    b = await _bought(admin_client, larch, honey, (2026, 6, 1), "9.50", "f2")
    c = await _bought(admin_client, larch, chile, (2026, 4, 1), "9.50", "f3")
    d = await _bought(admin_client, pier, honey, (2026, 7, 1), "1.25", "f4")

    def ids(rows):
        return [r["id"] for r in rows]

    # Store, location, and item words; accents folded; each purchase once.
    assert ids(await _all_pages(admin_client, q="larch")) == [b, c]
    assert ids(await _all_pages(admin_client, q="EAST")) == [b, c]
    assert ids(await _all_pages(admin_client, q="jalapeno")) == [a, c]
    assert ids(await _all_pages(admin_client, q="larch jalapeño")) == [c]
    assert ids(await _all_pages(admin_client, q="100%")) == []

    # Total, largest first; the tie at 9.50 falls back to newest first.
    assert ids(await _all_pages(admin_client, sort="total")) == [b, c, a, d]
    assert ids(await _all_pages(admin_client, sort="total", dir="asc")) == [d, a, b, c]
    # Where, A to Z, newest first within a store.
    assert ids(await _all_pages(admin_client, sort="where", dir="asc")) == [b, c, d, a]
    assert ids(await _all_pages(admin_client, sort="where", dir="desc")) == [d, a, b, c]
    # Date, oldest first.
    assert ids(await _all_pages(admin_client, sort="date", dir="asc")) == [c, a, b, d]

    # A cursor belongs to its sort; an unknown sort is refused.
    first = (await admin_client.get("/api/v1/purchases", params={"limit": 2})).json()
    r = await admin_client.get(
        "/api/v1/purchases", params={"sort": "total", "cursor": first["next_cursor"]}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_cursor"
    r = await admin_client.get("/api/v1/purchases", params={"sort": "lines"})
    assert r.status_code == 422
