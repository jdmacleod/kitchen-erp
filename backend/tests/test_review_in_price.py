"""A saving already in its item's price (2S): marked in review, kept, counted as nothing.

Every purchase here is invented.
"""

from __future__ import annotations

from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase

LINES = [
    {"raw_text": "MEADOW TORTILLAS 3.49 2.99 S", "line_total": "2.99"},
    {"raw_text": "Club Savings -0.50", "line_kind": "discount", "line_total": "0.50", "parent": 1},
]


async def _draft(admin_client, admin):
    loc = await make_location(admin_client, "Larkspur Grocers", "Larkspur Harbour")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES, total="2.99")
    body = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    return pid, sorted(body["lines"], key=lambda x: x["seq"])


async def _patch(admin_client, pid, line_id, **body):
    return await admin_client.patch(f"/api/v1/purchases/{pid}/lines/{line_id}", json=body)


async def test_a_saving_marked_in_price_keeps_its_amount_and_the_receipt_adds_up(
    admin_client, admin
):
    pid, (_, saving) = await _draft(admin_client, admin)
    before = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    assert before["trust"] == "check_lines"
    r = await _patch(admin_client, pid, saving["id"], in_price=True)
    assert r.status_code == 200, r.text
    body = r.json()
    line = next(x for x in body["lines"] if x["id"] == saving["id"])
    assert (line["line_total"], line["in_price_amount"]) == ("0.0000", "0.5000")
    assert "saving_in_price" in line["flags"]
    assert body["trust"] == "adds_up"


async def test_counting_it_again_restores_the_amount(admin_client, admin):
    pid, (_, saving) = await _draft(admin_client, admin)
    await _patch(admin_client, pid, saving["id"], in_price=True)
    body = (await _patch(admin_client, pid, saving["id"], in_price=False)).json()
    line = next(x for x in body["lines"] if x["id"] == saving["id"])
    assert (line["line_total"], line["in_price_amount"]) == ("0.5000", None)
    assert "saving_in_price" not in line["flags"]


async def test_giving_an_in_price_saving_an_amount_counts_it_again(admin_client, admin):
    pid, (_, saving) = await _draft(admin_client, admin)
    await _patch(admin_client, pid, saving["id"], in_price=True)
    body = (await _patch(admin_client, pid, saving["id"], line_total="0.40")).json()
    line = next(x for x in body["lines"] if x["id"] == saving["id"])
    assert (line["line_total"], line["in_price_amount"]) == ("0.4000", None)


async def test_only_a_saving_can_be_in_price(admin_client, admin):
    pid, (item, _) = await _draft(admin_client, admin)
    r = await _patch(admin_client, pid, item["id"], in_price=True)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "not_a_saving"
