"""Pieces in a pack (2026-10-05): a weighed pack may also say how many pieces it holds."""

from decimal import Decimal

import asyncpg
import httpx
import pytest

from tests.pricebook_helpers import make_location, make_product, shelf

D = Decimal


async def test_a_product_keeps_its_size_and_its_pieces(admin_client: httpx.AsyncClient):
    links = await make_product(
        admin_client,
        "Breakfast links",
        "Maple breakfast links",
        pack_qty="14",
        pack_unit="oz",
        pack_count=4,
        piece_name=" Link ",
    )
    assert (links["pack_qty"], links["pack_unit"]) == ("14", "oz")
    assert (links["pack_count"], links["piece_name"]) == (4, "link")

    # A new size keeps the pieces; clearing the pack clears them too.
    r = await admin_client.patch(
        f"/api/v1/products/{links['id']}", json={"pack_qty": "16", "pack_unit": "oz"}
    )
    assert r.json()["pack_count"] == 4
    r = await admin_client.patch(f"/api/v1/products/{links['id']}", json={"clear_pieces": True})
    assert (r.json()["pack_count"], r.json()["piece_name"]) == (None, None)
    r = await admin_client.patch(
        f"/api/v1/products/{links['id']}", json={"pack_count": 6, "piece_name": "link"}
    )
    assert r.json()["pack_count"] == 6
    r = await admin_client.patch(f"/api/v1/products/{links['id']}", json={"clear_pack": True})
    assert (r.json()["pack_qty"], r.json()["pack_count"], r.json()["piece_name"]) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"pack_count": 4},  # no pack at all
        {"pack_qty": "12", "pack_unit": "each", "pack_count": 4},  # a count pack
    ],
)
async def test_pieces_need_a_weight_or_volume(admin_client, body):
    links = await make_product(admin_client, "Breakfast links", "Plain links")
    r = await admin_client.patch(f"/api/v1/products/{links['id']}", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "pieces_need_size"
    after = (await admin_client.get(f"/api/v1/products/{links['id']}")).json()
    assert after["pack_count"] is None


async def test_a_piece_name_needs_a_count(admin_client):
    links = await make_product(admin_client, "Breakfast links", "Plain links")
    r = await admin_client.patch(
        f"/api/v1/products/{links['id']}",
        json={"pack_qty": "14", "pack_unit": "oz", "piece_name": "link"},
    )
    assert r.status_code == 422


async def test_the_database_refuses_pieces_without_a_pack(
    admin_client, owner_conn: asyncpg.Connection
):
    links = await make_product(admin_client, "Breakfast links", "Plain links")
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "UPDATE product SET pack_count = 4 WHERE id = $1",
            __import__("uuid").UUID(links["id"]),
        )


async def test_a_pack_price_becomes_a_price_per_piece(admin_client):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    links = await make_product(
        admin_client,
        "Breakfast links",
        "Maple links",
        canonical_unit="each",
        pack_qty="14",
        pack_unit="oz",
    )
    o = await shelf(admin_client, links["id"], loc["id"], "5.20")
    assert o["norm"]["status"] == "unknown_measure"

    # Saying how many pieces the pack holds re-prices what was already recorded.
    r = await admin_client.patch(
        f"/api/v1/products/{links['id']}", json={"pack_count": 4, "piece_name": "link"}
    )
    assert r.status_code == 200, r.text
    after = (await admin_client.get(f"/api/v1/price-observations/{o['id']}")).json()
    assert after["norm"]["status"] == "ok"
    assert after["norm"]["bridge_kind"] == "pack_count"
    assert after["norm"]["canonical_qty"] == "4" and after["norm"]["norm_unit"] == "each"
    assert D(after["norm"]["norm_unit_price"]) == D("1.300000")
