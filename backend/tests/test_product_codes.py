"""A product's codes on its page, and forgetting a remembered one (03, 1H; #448).

The store, wording and codes are invented. Weighed-item labels get computed check
digits.
"""

from __future__ import annotations

import asyncpg

from tests.pricebook_helpers import make_location, make_product
from tests.test_shared_weighed_codes import CHEESE_A, CODE, _identify, _lines, _waiting

CHEESE_C = {
    "raw_text": CHEESE_A["raw_text"].replace("CHEESE ONE", "CHEESE THREE"),
    "line_total": "5.20",
}


async def _remembered(admin_client, admin, db_session) -> tuple[dict, dict, str]:
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese = await make_product(
        admin_client, "Cheese A", "Counter cheese A", barcode="0123456789012"
    )
    purchase = await _waiting(admin_client, admin, db_session, loc, [CHEESE_A])
    line = (await _lines(admin_client, purchase))["CTR CHEESE ONE"][0]
    await _identify(admin_client, purchase, line, cheese)
    r = await admin_client.post(f"/api/v1/purchases/{purchase}/lines/{line['id']}/remember-code")
    assert r.status_code == 200, r.text
    return loc, cheese, purchase


async def test_the_codes_list_names_the_barcode_first_and_the_store_of_a_store_code(
    admin_client, admin, db_session
):
    _, cheese, _ = await _remembered(admin_client, admin, db_session)
    r = await admin_client.get(f"/api/v1/products/{cheese['id']}/codes")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [(c["scheme"], c["is_barcode"]) for c in items] == [("gtin", True), ("rw_item", False)]
    assert items[0]["value"] == cheese["barcode"]  # shown as the barcode field shows it
    assert items[1] | {"id": None, "created_at": None, "vendor_id": None} == {
        "id": None,
        "scheme": "rw_item",
        "value": CODE,
        "vendor_id": None,
        "vendor_name": "Juniper Market",
        "source": "receipt",
        "is_barcode": False,
        "created_at": None,
    }


async def test_forgetting_a_code_stops_it_identifying_and_keeps_the_lines_it_identified(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    loc, cheese, purchase = await _remembered(admin_client, admin, db_session)
    codes = (await admin_client.get(f"/api/v1/products/{cheese['id']}/codes")).json()["items"]
    store_code = next(c for c in codes if c["scheme"] == "rw_item")

    r = await admin_client.delete(f"/api/v1/products/{cheese['id']}/codes/{store_code['id']}")
    assert r.status_code == 204, r.text
    count = f"SELECT count(*) FROM product_identifier WHERE value = '{CODE}'"
    assert await owner_conn.fetchval(count) == 0
    # The line it identified keeps its product and price.
    (line,) = (await _lines(admin_client, purchase))["CTR CHEESE ONE"]
    assert line["product"]["id"] == cheese["id"]

    # A new line printing the code with other wording is no longer offered the product.
    later = await _waiting(admin_client, admin, db_session, loc, [CHEESE_C])
    (other,) = (await _lines(admin_client, later))["CTR CHEESE THREE"]
    assert other["resolution"] == "unmatched"
    assert all(s.get("kind") != "code" for s in other["suggestions"])


async def test_the_barcode_is_cleared_in_the_form_and_another_products_code_is_not_found(
    admin_client, admin, db_session
):
    _, cheese, _ = await _remembered(admin_client, admin, db_session)
    other = await make_product(admin_client, "Cheese B", "Counter cheese B")
    codes = (await admin_client.get(f"/api/v1/products/{cheese['id']}/codes")).json()["items"]
    barcode = next(c for c in codes if c["is_barcode"])
    store_code = next(c for c in codes if not c["is_barcode"])

    r = await admin_client.delete(f"/api/v1/products/{cheese['id']}/codes/{barcode['id']}")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "barcode_field"
    r = await admin_client.delete(f"/api/v1/products/{other['id']}/codes/{store_code['id']}")
    assert r.status_code == 404, r.text
    r = await admin_client.get(f"/api/v1/products/{cheese['id']}/codes")
    assert len(r.json()["items"]) == 2
