"""Review's "merge into the next line" for a weight or count on a row of its own (#87).

The lines stage joins such a row to its item when the printed arithmetic proves
where it belongs. When it cannot, the row reaches review as a line of its own,
flagged ``quantity_line``, and a person merges it in one action.
"""

from __future__ import annotations

from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase


async def _draft(admin_client, admin, lines, total) -> tuple[str, list[dict]]:
    loc = await make_location(admin_client, "Quayside Grocer", "Quayside Grocer North")
    purchase_id = await make_receipt_purchase(admin.id, loc["id"], lines, total=total)
    body = (await admin_client.get(f"/api/v1/purchases/{purchase_id}")).json()
    return purchase_id, sorted(body["lines"], key=lambda x: x["seq"])


async def _merge(admin_client, purchase_id, line, into):
    return await admin_client.post(
        f"/api/v1/purchases/{purchase_id}/lines/{line['id']}/merge",
        json={"into_line_id": into["id"]},
    )


async def test_a_count_merges_into_the_next_line_and_the_total_reconciles(admin_client, admin):
    p, (count, avocado, milk) = await _draft(
        admin_client,
        admin,
        [
            {
                "raw_text": "5 @ 0.79",
                "line_total": "3.95",
                "qty": "5",
                "unit": "each",
                "unit_price": "0.79",
                "flags": ["qty_inferred", "quantity_line"],
            },
            {
                "raw_text": "AVOCADO 3.95 F",
                "line_total": "3.95",
                "qty": "1",
                "unit": "each",
                "flags": ["qty_assumed"],
            },
            {"raw_text": "OAT MILK 3.49", "line_total": "3.49", "qty": "1", "unit": "each"},
        ],
        total="7.44",
    )
    r = await _merge(admin_client, p, count, avocado)
    assert r.status_code == 200, r.text
    body = r.json()
    lines = sorted(body["lines"], key=lambda x: x["seq"])
    assert [ln["raw_text"] for ln in lines] == ["AVOCADO 3.95 F", "OAT MILK 3.49"]
    merged = lines[0]
    assert (merged["qty"], merged["unit"], merged["unit_price"], merged["line_total"]) == (
        "5",
        "each",
        "0.7900",
        "3.9500",
    )
    assert merged["flags"] == []  # a person said where the quantity belongs
    assert "total_mismatch" not in body["flags"]


async def test_a_weight_with_its_amount_gives_a_name_row_its_price(admin_client, admin):
    p, (bananas, weight) = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "BANANAS", "line_total": "0.00", "qty": "1", "unit": "each"},
            {
                "raw_text": "2.31 lb @ 0.69/lb   1.59",
                "line_total": "1.59",
                "qty": "2.31",
                "unit": "lb",
                "unit_price": "0.69",
                "flags": ["quantity_line"],
            },
        ],
        total="1.59",
    )
    r = await _merge(admin_client, p, weight, bananas)
    assert r.status_code == 200, r.text
    [line] = r.json()["lines"]
    assert (line["raw_text"], line["qty"], line["unit"], line["line_total"]) == (
        "BANANAS",
        "2.31",
        "lb",
        "1.5900",
    )


async def test_a_saving_attached_to_the_quantity_row_moves_to_the_item(admin_client, admin):
    p, (count, saving, avocado) = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "5 @ 0.79", "line_total": "3.95", "flags": ["quantity_line"]},
            {
                "raw_text": "MEMBER SAVINGS 0.50-",
                "line_total": "0.50",
                "line_kind": "discount",
                "parent": 1,
            },
            {"raw_text": "AVOCADO 3.95 F", "line_total": "3.95"},
        ],
        total="3.45",
    )
    r = await _merge(admin_client, p, count, avocado)
    assert r.status_code == 200, r.text
    lines = {ln["raw_text"]: ln for ln in r.json()["lines"]}
    assert lines["MEMBER SAVINGS 0.50-"]["parent_line_id"] == avocado["id"]


async def test_only_a_quantity_row_merges_and_only_into_an_item(admin_client, admin):
    p, (milk, count, saving) = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "OAT MILK 3.49", "line_total": "3.49"},
            {"raw_text": "5 @ 0.79", "line_total": "3.95"},
            {"raw_text": "CARD SAVINGS 0.20-", "line_total": "0.20", "line_kind": "discount"},
        ],
        total="7.24",
    )
    r = await _merge(admin_client, p, milk, count)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "not_a_quantity")
    r = await _merge(admin_client, p, count, saving)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "not_an_item")
    r = await _merge(admin_client, p, count, count)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "self_merge")
    r = await admin_client.post(
        f"/api/v1/purchases/{p}/lines/{count['id']}/merge",
        json={"into_line_id": "00000000-0000-4000-8000-000000000000"},
    )
    assert r.status_code == 404


async def test_a_committed_purchase_is_reopened_before_a_merge(admin_client, admin, owner_conn):
    p, (count, avocado) = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "5 @ 0.79", "line_total": "3.95"},
            {"raw_text": "AVOCADO 3.95 F", "line_total": "3.95"},
        ],
        total="3.95",
    )
    await owner_conn.execute("UPDATE purchase SET status = 'committed' WHERE id = $1::uuid", p)
    r = await _merge(admin_client, p, count, avocado)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "committed")
