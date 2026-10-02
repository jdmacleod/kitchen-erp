"""Receipt lines matched by item code (04, 2K): criteria 70–72.

The warehouse, its receipt and its codes are invented. Weighed-item labels get
computed check digits.
"""

from __future__ import annotations

import json
import string
import uuid

import asyncpg

from app.catalog import receipt_codes
from app.catalog.identifiers import check_digit
from tests.pricebook_helpers import make_location, make_product, shelf
from tests.resolution_helpers import make_receipt_purchase
from tests.test_resolution import resolve

LEADING = {"kind": "leading_token", "min_len": 5, "max_len": 7}


def label(body: str) -> str:
    digits = body.replace(" ", "")
    return digits + str(check_digit(digits))


# A synthetic warehouse-style receipt: item codes lead each line.
WAREHOUSE = [
    {
        "raw_text": "77123 KS ROLLED OATS 10LB 12.49",
        "line_total": "12.49",
        "qty": "1",
        "unit": "each",
    },
    {"raw_text": "51004 KS OLIVE OIL 2L 18.99", "line_total": "18.99", "qty": "1", "unit": "each"},
    {"raw_text": "88001 ORG BANANAS 3LB 1.99", "line_total": "1.99", "qty": "1", "unit": "each"},
]


async def warehouse(admin_client, owner_conn, *, code_position=LEADING) -> dict:
    loc = await make_location(admin_client, "Bayside Warehouse", "Bayside Warehouse")
    await owner_conn.execute(
        "UPDATE vendor SET code_position = $1::jsonb WHERE id = $2",
        json.dumps(code_position) if code_position else None,
        uuid.UUID(loc["vendor"]["id"]),
    )
    return loc


async def remember(owner_conn, product_id: str, scheme: str, value: str, vendor_id: str) -> None:
    await owner_conn.execute(
        "INSERT INTO product_identifier (id, product_id, scheme, value, vendor_id, source) "
        "VALUES (gen_random_uuid(), $1, $2, $3, $4, 'manual')",
        uuid.UUID(product_id),
        scheme,
        value,
        uuid.UUID(vendor_id),
    )


def test_codes_are_read_from_their_position_only():
    found = receipt_codes.read("77123 KS ROLLED OATS 12.49", LEADING, None)
    assert ("vendor_sku", "77123") in found.positioned and found.offer == ("vendor_sku", "77123")
    elsewhere = receipt_codes.read("KS ROLLED OATS 77123 12.49", LEADING, None)
    assert elsewhere.positioned == () and "77123" in elsewhere.loose
    no_position = receipt_codes.read("77123 KS ROLLED OATS 12.49", None, None)
    assert no_position.positioned == () and "77123" in no_position.loose
    too_long = receipt_codes.read(f"{string.digits} THING 1.00", LEADING, None)
    assert too_long.positioned == ()


def test_a_weighed_label_is_its_item_code():
    code = label("2 04123 00349")
    found = receipt_codes.read(f"{code} DELI HAM 3.49", None, None)
    assert ("rw_item", "04123") in found.positioned


def test_reading_a_hostile_line_stays_linear():
    import time

    started = time.monotonic()
    receipt_codes.read(" " * 50_000 + "9" * 50_000, LEADING, None)
    assert time.monotonic() - started < 1


async def test_a_leading_code_resolves_by_identifier_and_flags_an_outlier(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    """Criterion 70."""
    loc = await warehouse(admin_client, owner_conn)
    oats = await make_product(
        admin_client, "Oats", "Rolled oats 10 lb", pack_qty="10", pack_unit="lb"
    )
    await remember(owner_conn, oats["id"], "rw_item", "77123", loc["vendor"]["id"])
    await shelf(admin_client, oats["id"], loc["id"], "12.49")

    purchase = await make_receipt_purchase(admin.id, loc["id"], WAREHOUSE)
    lines = (await resolve(admin_client, purchase, db_session))["lines"]
    assert lines[0]["resolution"] == "identifier" and lines[0]["product"]["id"] == oats["id"]
    assert lines[0]["resolved_by"] is None and "price_outlier" not in lines[0]["flags"]
    assert lines[1]["resolution"] == "unmatched"

    # The same code at five times the price: resolved, and flagged as for aliases.
    dear = [{**WAREHOUSE[0], "raw_text": "77123 KS ROLLED OATS 10LB 62.45", "line_total": "62.45"}]
    again = await make_receipt_purchase(admin.id, loc["id"], dear)
    [line] = (await resolve(admin_client, again, db_session))["lines"]
    assert line["resolution"] == "identifier" and "price_outlier" in line["flags"]


async def test_a_code_elsewhere_or_without_a_position_only_suggests(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    """Criterion 71."""
    loc = await warehouse(admin_client, owner_conn)
    oil = await make_product(admin_client, "Olive oil", "Olive oil 2 L")
    await remember(owner_conn, oil["id"], "vendor_sku", "51004", loc["vendor"]["id"])
    moved = [{**WAREHOUSE[1], "raw_text": "KS OLIVE OIL 2L 51004 18.99"}]
    purchase = await make_receipt_purchase(admin.id, loc["id"], moved)
    [line] = (await resolve(admin_client, purchase, db_session))["lines"]
    assert line["resolution"] == "unmatched" and line["product"] is None
    assert [s["product_id"] for s in line["suggestions"] if s["kind"] == "code"] == [oil["id"]]

    other = await make_location(admin_client, "Plain Grocer", "Plain Grocer")
    await remember(owner_conn, oil["id"], "vendor_sku", "51004", other["vendor"]["id"])
    purchase = await make_receipt_purchase(admin.id, other["id"], [WAREHOUSE[1]])
    [line] = (await resolve(admin_client, purchase, db_session))["lines"]
    assert line["resolution"] == "unmatched"
    assert [s["kind"] for s in line["suggestions"]] == ["code"]


async def test_identifying_a_line_offers_its_code_and_records_it_only_on_a_click(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    """Criterion 72."""
    loc = await warehouse(admin_client, owner_conn)
    bananas = await make_product(admin_client, "Bananas", "Organic bananas")
    purchase = await make_receipt_purchase(admin.id, loc["id"], [WAREHOUSE[2]])
    [line] = (await resolve(admin_client, purchase, db_session))["lines"]
    assert line["code_offer"] is None  # nothing to remember until a product is chosen

    r = await admin_client.post(
        f"/api/v1/purchases/{purchase}/lines/{line['id']}/resolve",
        json={"product_id": bananas["id"]},
    )
    assert r.status_code == 200, r.text
    [line] = r.json()["lines"]
    assert line["code_offer"] == {"scheme": "vendor_sku", "value": "88001"}
    count = "SELECT count(*) FROM product_identifier WHERE value = '88001'"
    assert await owner_conn.fetchval(count) == 0  # not without the click

    r = await admin_client.post(f"/api/v1/purchases/{purchase}/lines/{line['id']}/remember-code")
    assert r.status_code == 200, r.text
    assert r.json() == {"scheme": "vendor_sku", "value": "88001", "product_id": bananas["id"]}
    assert await owner_conn.fetchval(count) == 1
    [line] = (await admin_client.get(f"/api/v1/purchases/{purchase}")).json()["lines"]
    assert line["code_offer"] is None

    # The next receipt resolves by the code.
    later = await make_receipt_purchase(admin.id, loc["id"], [WAREHOUSE[2]])
    [line] = (await resolve(admin_client, later, db_session))["lines"]
    assert line["resolution"] == "identifier" and line["product"]["id"] == bananas["id"]


async def test_the_to_identify_queue_shows_the_code_to_remember(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    """Criterion 72, from the to-identify queue."""
    loc = await warehouse(admin_client, owner_conn)
    bananas = await make_product(admin_client, "Bananas", "Organic bananas")
    purchase = await make_receipt_purchase(admin.id, loc["id"], [WAREHOUSE[2]])
    await resolve(admin_client, purchase, db_session)
    r = await admin_client.post(f"/api/v1/purchases/{purchase}/commit")
    assert r.status_code == 200, r.text
    [group] = (await admin_client.get("/api/v1/to-identify")).json()["items"]
    assert group["code"] == {"scheme": "vendor_sku", "value": "88001"}
    r = await admin_client.post(
        "/api/v1/to-identify/apply",
        json={
            "vendor_id": group["vendor"]["id"],
            "raw_text_norm": group["raw_text_norm"],
            "product_id": bananas["id"],
        },
    )
    assert r.status_code == 200, r.text
    line = group["lines"][0]
    r = await admin_client.post(
        f"/api/v1/purchases/{line['purchase_id']}/lines/{line['line_id']}/remember-code"
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "88001"
