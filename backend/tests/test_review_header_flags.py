"""A value the receipt reader could not find stops being flagged once a person gives it.

The lines stage marks a draft `purchased_at_missing` (the upload time stands in for
the date) and `total_missing` (the line sum stands in for the printed total). A
header edit that supplies the value clears that flag and only that flag, so a
corrected purchase does not carry the warning onto its committed page.
"""

from __future__ import annotations

from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase

LINE = {"raw_text": "OAT MILK 3.49", "line_total": "3.49", "qty": "1", "unit": "each"}


async def _flagged_draft(admin_client, admin, owner_conn) -> str:
    loc = await make_location(admin_client, "Quayside Grocer", "Quayside Grocer North")
    purchase_id = await make_receipt_purchase(admin.id, loc["id"], [LINE])
    await owner_conn.execute(
        "UPDATE purchase SET flags = $1 WHERE id = $2::uuid",
        ["purchased_at_missing", "total_missing", "reconcile_mismatch"],
        purchase_id,
    )
    return purchase_id


async def test_giving_the_date_clears_only_the_date_flag(admin_client, admin, owner_conn):
    p = await _flagged_draft(admin_client, admin, owner_conn)
    r = await admin_client.patch(
        f"/api/v1/purchases/{p}", json={"purchased_at": "2026-05-02T17:30:00Z"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["flags"] == ["total_missing", "reconcile_mismatch"]


async def test_giving_the_total_clears_only_the_total_flag(admin_client, admin, owner_conn):
    p = await _flagged_draft(admin_client, admin, owner_conn)
    r = await admin_client.patch(f"/api/v1/purchases/{p}", json={"total": "3.49"})
    assert r.status_code == 200, r.text
    # The typed total agrees with the line, so the old mismatch goes too.
    assert r.json()["flags"] == ["purchased_at_missing"]


async def test_an_unrelated_edit_leaves_both_flags(admin_client, admin, owner_conn):
    p = await _flagged_draft(admin_client, admin, owner_conn)
    r = await admin_client.patch(f"/api/v1/purchases/{p}", json={"subtotal": "3.49"})
    assert r.status_code == 200, r.text
    assert set(r.json()["flags"]) == {"purchased_at_missing", "total_missing", "reconcile_mismatch"}


async def test_a_typed_total_that_disagrees_with_the_lines_is_a_mismatch(
    admin_client, admin, owner_conn
):
    # The reader found no total, so nothing compared the lines with the receipt.
    # A misread line ("4.49" read as "449") shows up once the printed total is typed.
    p = await _flagged_draft(admin_client, admin, owner_conn)
    await owner_conn.execute(
        "UPDATE purchase SET flags = $1 WHERE id = $2::uuid", ["total_missing"], p
    )
    r = await admin_client.patch(f"/api/v1/purchases/{p}", json={"total": "41.07"})
    assert r.status_code == 200, r.text
    assert r.json()["flags"] == ["total_mismatch"]
    # Header tax counts when no line carries it.
    r = await admin_client.patch(f"/api/v1/purchases/{p}", json={"total": "3.79", "tax": "0.30"})
    assert r.json()["flags"] == []
