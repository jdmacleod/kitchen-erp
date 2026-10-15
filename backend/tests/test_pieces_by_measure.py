"""A pack counted in pieces is priced through the ingredient's piece measure (issue 357)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from tests.catalog_helpers import make_ingredient, make_product
from tests.pricebook_helpers import make_location, shelf


async def _rows(client) -> list[dict]:
    r = await client.get("/api/v1/price-book/needs-bridge")
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_weighing_one_piece_prices_every_pack_in_pieces(admin_client, owner_conn):
    loc = await make_location(admin_client, "Tarn Market", "Tarn Market, Quayside")
    limes = await make_ingredient(admin_client, "Tarn limes")
    bag = await make_product(
        admin_client, limes["id"], "Tarn limes, bag", pack_qty="8", pack_unit="each"
    )
    single = await make_product(
        admin_client, limes["id"], "Tarn lime", pack_qty="1", pack_unit="each"
    )
    await shelf(admin_client, bag["id"], loc["id"], "4.00")
    await shelf(admin_client, single["id"], loc["id"], "0.60")
    assert {r["product"]["id"]: r["status"] for r in await _rows(admin_client)} == {
        bag["id"]: "no_pack",
        single["id"]: "no_pack",
    }

    r = await admin_client.post(
        f"/api/v1/ingredients/{limes['id']}/measures",
        json={"label": "each", "canonical_qty": "67", "source": "measured", "confirmed": True},
    )
    assert r.status_code == 201, r.text
    assert await _rows(admin_client) == []
    norm = await owner_conn.fetch(
        "SELECT p.name, pn.canonical_qty, pn.norm_unit_price FROM price_norm pn "
        "JOIN price_observation o ON o.id = pn.observation_id "
        "JOIN product p ON p.id = o.product_id WHERE p.ingredient_id = $1 ORDER BY p.name",
        uuid.UUID(limes["id"]),
    )
    got = {n["name"]: (n["canonical_qty"], n["norm_unit_price"]) for n in norm}
    assert got["Tarn limes, bag"][0] == Decimal("536")
    assert got["Tarn lime"][0] == Decimal("67")
