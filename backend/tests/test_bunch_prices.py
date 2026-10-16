"""Prices per bunch (issue 371): compared per bunch until a bunch is weighed, then by weight.

Every vendor, product and price here is invented.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.services import pricebook
from tests.catalog_helpers import make_ingredient, make_product
from tests.pricebook_helpers import make_location, shelf


async def _rows(client) -> list[dict]:
    r = await client.get("/api/v1/price-book/needs-bridge")
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _norms(owner_conn, ingredient_id: str) -> list[dict]:
    rows = await owner_conn.fetch(
        "SELECT pn.observation_id, pn.canonical_qty, pn.norm_unit, pn.norm_unit_price, "
        "pn.status, pn.bridge_kind, pn.bridge_source, pn.bridge_confirmed, pn.convert_version "
        "FROM price_norm pn JOIN price_observation o ON o.id = pn.observation_id "
        "JOIN product p ON p.id = o.product_id WHERE p.ingredient_id = $1 "
        "ORDER BY pn.observation_id",
        uuid.UUID(ingredient_id),
    )
    return [dict(r) for r in rows]


async def _setup(client):
    north = await make_location(client, "Quill Grocers", "Quill Grocers, Northgate")
    south = await make_location(client, "Fenwick Farm Stand", "Fenwick Farm Stand, Southmead")
    herb = await make_ingredient(client, "Vellum cress", category="produce")
    bunch = await make_product(client, herb["id"], "Vellum cress", pack_qty="1", pack_unit="bunch")
    loose = await make_product(client, herb["id"], "Vellum cress, loose")
    return north, south, herb, bunch, loose


async def test_bunch_prices_compare_per_bunch_until_weighed(admin_client, owner_conn, db_session):
    north, south, herb, bunch, loose = await _setup(admin_client)
    await shelf(admin_client, bunch["id"], north["id"], "1.29")
    await shelf(admin_client, loose["id"], south["id"], "1.10", unit="bunch")
    await shelf(admin_client, loose["id"], north["id"], "3.00", qty="1", unit="lb")

    rows = await _rows(admin_client)
    assert {(r["product"]["id"], r["status"]) for r in rows} == {
        (bunch["id"], "no_bunch_weight"),
        (loose["id"], "no_bunch_weight"),
    }
    norms = await _norms(owner_conn, herb["id"])
    per_bunch = sorted(n["norm_unit_price"] for n in norms if n["norm_unit"] == "bunch")
    assert per_bunch == [Decimal("1.100000"), Decimal("1.290000")]

    # Offers: the per-lb price first, then the per-bunch ones, each read in its own unit.
    r = await admin_client.get(f"/api/v1/ingredients/{herb['id']}/offers")
    offers = r.json()["items"]
    assert [(o["display_unit"], o["display_unit_price"]) for o in offers] == [
        ("lb", "3.00"),
        ("bunch", "1.10"),
        ("bunch", "1.29"),
    ]

    # Compare: one cell per vendor, the canonical unit preferred; cheapest within a unit.
    r = await admin_client.post("/api/v1/price-book/compare", json={"ingredient_ids": [herb["id"]]})
    cells = r.json()["rows"][0]["cells"]
    by_unit = {c["display_unit"]: c for c in cells.values()}
    assert set(by_unit) == {"lb", "bunch"}
    assert by_unit["bunch"]["display_unit_price"] == "1.10" and by_unit["bunch"]["cheapest"]
    assert by_unit["lb"]["cheapest"]

    # The history chart and the map show one unit: the canonical one.
    r = await admin_client.get(f"/api/v1/ingredients/{herb['id']}/price-history")
    assert {p["display_unit"] for p in r.json()["points"]} == {"lb"}
    r = await admin_client.get("/api/v1/price-book/cheapest", params={"ingredient_id": herb["id"]})
    assert r.json()["unit"] == "g"

    # Weigh one bunch: every bunch of the ingredient now joins the per-lb prices.
    r = await admin_client.post(
        f"/api/v1/ingredients/{herb['id']}/measures",
        json={"label": "bunch", "canonical_qty": "90", "source": "measured", "confirmed": True},
    )
    assert r.status_code == 201, r.text
    assert await _rows(admin_client) == []
    norms = await _norms(owner_conn, herb["id"])
    assert {n["norm_unit"] for n in norms} == {"g"} and {n["status"] for n in norms} == {"ok"}
    assert {n["canonical_qty"] for n in norms} >= {Decimal("90")}
    r = await admin_client.get(f"/api/v1/ingredients/{herb['id']}/offers")
    assert {o["display_unit"] for o in r.json()["items"]} == {"oz"}

    # Derived and rebuildable: a full recompute gives the same rows.
    await pricebook.recompute_all(db_session)
    assert await _norms(owner_conn, herb["id"]) == norms


async def test_only_bunches_chart_and_map_per_bunch(admin_client):
    north, south, herb, bunch, _ = await _setup(admin_client)
    await shelf(admin_client, bunch["id"], north["id"], "1.29")
    await shelf(admin_client, bunch["id"], south["id"], "0.99")
    r = await admin_client.get(f"/api/v1/ingredients/{herb['id']}/price-history")
    body = r.json()
    assert body["display_unit"] == "bunch" and body["display_low"] == "0.990"
    r = await admin_client.get("/api/v1/price-book/cheapest", params={"ingredient_id": herb["id"]})
    assert r.json()["unit"] == "bunch" and r.json()["display_unit"] == "bunch"


async def test_recompute_is_identical_before_weighing(admin_client, owner_conn, db_session):
    north, _, herb, bunch, _ = await _setup(admin_client)
    await shelf(admin_client, bunch["id"], north["id"], "2.58", qty="2")
    before = await _norms(owner_conn, herb["id"])
    assert [(n["canonical_qty"], n["norm_unit"]) for n in before] == [(Decimal("2"), "bunch")]
    await pricebook.recompute_all(db_session)
    assert await _norms(owner_conn, herb["id"]) == before


async def test_a_bunch_takes_no_pieces(admin_client):
    herb = await make_ingredient(admin_client, "Vellum cress", category="produce")
    r = await admin_client.post(
        "/api/v1/products",
        json={
            "ingredient_id": herb["id"],
            "name": "Vellum cress",
            "pack_qty": "1",
            "pack_unit": "bunch",
            "pack_count": 6,
            "allow_duplicate": True,
        },
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "pieces_need_size"


async def test_inbox_says_weigh_bunches(admin_client):
    north, _, _, bunch, _ = await _setup(admin_client)
    await shelf(admin_client, bunch["id"], north["id"], "1.29")
    r = await admin_client.get("/api/v1/inbox")
    row = next(i for i in r.json()["items"] if i["kind"] == "bridge")
    assert row["title"] == "1 product needs a bunch weighed before its prices compare"
    assert row["action_label"] == "Weigh bunches"
