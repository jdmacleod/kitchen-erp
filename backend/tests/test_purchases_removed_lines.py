"""A removed line is invisible to every reader of a purchase's lines (#72, R8).

Such a line stays in the table only because its voided observation points at
it. Totals, the reconcile check, review, the inbox and the to-identify queue
must all skip it; the table and backup keep it.
"""

import uuid
from datetime import UTC, datetime

from tests.pricebook_helpers import make_location, make_product


async def _with_removed_line(client) -> tuple[dict, str]:
    """A two-line manual purchase whose second line was removed."""
    loc = await make_location(client, "Lantern Market", "Lantern Market")
    kept = await make_product(client, "Leeks", "Market leeks")
    gone = await make_product(client, "Kale", "Market kale")
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime(2026, 6, 3, 17, 0, tzinfo=UTC).isoformat(),
        "total": "5.00",
        "lines": [
            {"product_id": kept["id"], "qty": "1", "unit": "each", "line_total": "2.00"},
            {"product_id": gone["id"], "qty": "1", "unit": "each", "line_total": "3.00"},
        ],
    }
    p = (await client.post("/api/v1/purchases", json=body)).json()
    edit = {**body, "total": "2.00", "lines": [{**body["lines"][0], "id": p["lines"][0]["id"]}]}
    r = await client.put(f"/api/v1/purchases/{p['id']}", json=edit)
    assert r.status_code == 200, r.text
    return r.json(), p["lines"][1]["id"]


async def test_totals_and_the_reconcile_check_skip_it(admin_client):
    p, _ = await _with_removed_line(admin_client)
    assert p["computed_total"] == "2.0000"
    await admin_client.post(f"/api/v1/purchases/{p['id']}/reopen")
    # Typing the total again re-runs the check against the lines: 2.00 matches
    # only if the removed 3.00 is left out.
    r = await admin_client.patch(f"/api/v1/purchases/{p['id']}", json={"total": "2.00"})
    assert r.status_code == 200, r.text
    assert "total_mismatch" not in r.json()["flags"]


async def test_review_and_the_purchase_skip_it(admin_client):
    p, removed = await _with_removed_line(admin_client)
    got = (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()
    assert removed not in {ln["id"] for ln in got["lines"]}
    listed = (await admin_client.get("/api/v1/purchases")).json()["items"]
    assert all(removed not in {ln["id"] for ln in item["lines"]} for item in listed)
    # A line id that has been removed is not on the purchase any more.
    r = await admin_client.patch(
        f"/api/v1/purchases/{p['id']}/lines/{removed}", json={"line_total": "1.00"}
    )
    assert r.status_code in (404, 409)


async def test_the_inbox_skips_it(admin_client, owner_conn):
    p, _ = await _with_removed_line(admin_client)
    # Only a draft's inbox row counts its lines; put this one back to draft.
    pid = uuid.UUID(p["id"])
    await owner_conn.execute("UPDATE purchase SET status = 'draft' WHERE id = $1", pid)
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    row = next(i for i in items if i["action_route"] == f"/shop/purchases/{p['id']}")
    assert row["detail"] == "1 line ready to review and commit."


async def test_the_to_identify_queue_skips_it(admin_client, owner_conn):
    p, removed = await _with_removed_line(admin_client)
    # Make both lines unidentified with the same text: only the visible one queues.
    await owner_conn.execute(
        "UPDATE purchase_line SET product_id = NULL, resolution = 'unmatched', "
        "raw_text = 'MYSTERY ROOT', raw_text_norm = 'mystery root' WHERE purchase_id = $1",
        uuid.UUID(p["id"]),
    )
    groups = (await admin_client.get("/api/v1/to-identify")).json()["items"]
    assert [g["line_count"] for g in groups] == [1]
    assert removed not in {ln["line_id"] for ln in groups[0]["lines"]}


async def test_the_table_keeps_it(admin_client, owner_conn):
    p, removed = await _with_removed_line(admin_client)
    row = await owner_conn.fetchrow(
        "SELECT removed_at FROM purchase_line WHERE id = $1", uuid.UUID(removed)
    )
    assert row is not None and row["removed_at"] is not None
    # Its voided observation still points at it.
    n = await owner_conn.fetchval(
        "SELECT count(*) FROM price_observation WHERE purchase_line_id = $1", uuid.UUID(removed)
    )
    assert n == 1
