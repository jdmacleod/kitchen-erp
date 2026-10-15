"""Codes and zero rows on receipts read with their printed rows (2S).

The model often leaves a row's code out of raw_text; the printed row keeps it.
The warehouse, its receipt and its codes are invented.
"""

from __future__ import annotations

import uuid

import asyncpg

from tests.pricebook_helpers import make_location, make_product
from tests.resolution_helpers import make_receipt_purchase
from tests.test_item_codes import LEADING, remember, warehouse
from tests.test_resolution import resolve


async def test_a_code_only_the_printed_row_keeps_still_resolves_by_identifier(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    loc = await warehouse(admin_client, owner_conn, code_position=LEADING)
    oats = await make_product(admin_client, "Oats", "Rolled oats 10 lb")
    await remember(owner_conn, oats["id"], "vendor_sku", "77123", loc["vendor"]["id"])
    stripped = {
        "raw_text": "KS ROLLED OATS 10LB",
        "printed_text": "77123 KS ROLLED OATS 10LB 12.49",
        "line_total": "12.49",
        "qty": "1",
        "unit": "each",
    }
    purchase = await make_receipt_purchase(admin.id, loc["id"], [stripped])
    [line] = (await resolve(admin_client, purchase, db_session))["lines"]
    assert line["resolution"] == "identifier" and line["product"]["id"] == oats["id"]


async def test_a_weight_row_kept_at_zero_is_never_queued_to_identify(
    admin_client, admin, owner_conn: asyncpg.Connection
):
    loc = await make_location(admin_client, "Larkspur Grocers", "Larkspur Harbour")
    lines = [
        {"raw_text": "PEPPERS FRESNO", "line_total": "0.63"},
        {
            "raw_text": "WT 0.07 lb @ $8.99 /lb",
            "line_total": "0",
            "qty": "0.07",
            "unit": "lb",
            "flags": ["quantity_line", "no_amount_printed"],
        },
    ]
    pid = await make_receipt_purchase(admin.id, loc["id"], lines)
    await owner_conn.execute(
        "UPDATE purchase SET status = 'committed' WHERE id = $1", uuid.UUID(pid)
    )
    queue = (await admin_client.get("/api/v1/to-identify")).json()["items"]
    texts = [line["raw_text"] for group in queue for line in group["lines"]]
    assert texts == ["PEPPERS FRESNO"]
