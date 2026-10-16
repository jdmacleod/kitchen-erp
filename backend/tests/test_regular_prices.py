"""Regular prices worked out when read (issue 381; spec 04, 2A and 2E).

A till that prints a regular price and what was paid on an item's row leaves the
saving beneath it already in the price. The observation records what was paid;
the regular price is read back from the purchase line. Every store, product and
amount here is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.pricebook_helpers import make_location, make_product, shelf
from tests.resolution_helpers import make_receipt_purchase


def _lines(flags: list[str], *, in_price: bool = True) -> list[dict]:
    saving = {
        "raw_text": "Club Savings 0.50",
        "line_kind": "discount",
        "line_total": "0" if in_price else "0.50",
        "flags": flags,
        "parent": 1,
    }
    if in_price:
        saving["in_price_amount"] = "0.50"
    return [{"raw_text": "MEADOW TORTILLAS 3.49 2.99 S", "line_total": "2.99"}, saving]


async def _setup(client, vendor: str = "Larkspur Grocers"):
    loc = await make_location(client, vendor, f"{vendor} Harbour")
    product = await make_product(
        client, "Flour tortillas", "Meadow tortillas", pack_qty="1", pack_unit="lb"
    )
    return loc, product


async def _commit(client, admin, loc, product, lines, **kw) -> str:
    pid = await make_receipt_purchase(admin.id, loc["id"], lines, total="2.99", **kw)
    body = (await client.get(f"/api/v1/purchases/{pid}")).json()
    item = min(body["lines"], key=lambda x: x["seq"])
    r = await client.post(
        f"/api/v1/purchases/{pid}/lines/{item['id']}/resolve", json={"product_id": product["id"]}
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200, r.text
    return pid


async def _history(client, product) -> dict:
    return (await client.get(f"/api/v1/products/{product['id']}/prices")).json()


async def test_a_saving_proven_on_its_row_gives_the_regular_price_beside_what_was_paid(
    admin_client, admin
):
    loc, product = await _setup(admin_client)
    await _commit(admin_client, admin, loc, product, _lines(["saving_in_price"]))
    h = await _history(admin_client, product)
    (point,) = h["points"]
    assert point["price"] == "2.9900"
    assert point["regular_price"] == "3.4900"
    assert (point["display_unit_price"], point["regular_display_unit_price"]) == ("2.99", "3.49")
    assert point["display_unit"] == "lb"
    assert point["is_promo"] is True
    (latest,) = h["latest"]
    assert (latest["price"], latest["regular_price"], latest["is_promo"]) == (
        "2.9900",
        "3.4900",
        True,
    )
    records = (
        await admin_client.get("/api/v1/price-observations", params={"product_id": product["id"]})
    ).json()["items"]
    assert [(o["price"], o["regular_price"], o["is_promo"]) for o in records] == [
        ("2.9900", "3.4900", True)
    ]
    one = (await admin_client.get(f"/api/v1/price-observations/{records[0]['id']}")).json()
    assert one["regular_price"] == "3.4900"


async def test_a_saving_read_one_digit_off_counts_as_proven_by_its_row(admin_client, admin):
    loc, product = await _setup(admin_client)
    flags = ["saving_in_price", "saving_amount_from_row"]
    await _commit(admin_client, admin, loc, product, _lines(flags))
    (point,) = (await _history(admin_client, product))["points"]
    assert point["regular_price"] == "3.4900"


async def test_savings_put_in_the_price_by_the_totals_give_no_regular_price(admin_client, admin):
    loc, product = await _setup(admin_client)
    await _commit(admin_client, admin, loc, product, _lines(["saving_in_price_by_total"]))
    h = await _history(admin_client, product)
    (point,) = h["points"]
    assert (point["price"], point["regular_price"]) == ("2.9900", None)
    assert point["regular_display_unit_price"] is None


async def test_a_you_saved_note_gives_no_regular_price(admin_client, admin):
    loc, product = await _setup(admin_client)
    await _commit(admin_client, admin, loc, product, _lines(["saving_already_netted"]))
    (point,) = (await _history(admin_client, product))["points"]
    assert point["regular_price"] is None


async def test_a_counted_saving_gives_no_regular_price(admin_client, admin):
    loc, product = await _setup(admin_client)
    await _commit(admin_client, admin, loc, product, _lines([], in_price=False))
    (point,) = (await _history(admin_client, product))["points"]
    assert (point["price"], point["regular_price"], point["is_promo"]) == ("2.4900", None, True)


async def test_imports_and_shelf_prices_have_no_regular_price(admin_client, admin):
    loc, product = await _setup(admin_client)
    await _commit(admin_client, admin, loc, product, _lines(["saving_in_price"]), source="import")
    await shelf(admin_client, product["id"], loc["id"], "3.10", is_promo=True)
    points = (await _history(admin_client, product))["points"]
    assert [(p["source"], p["regular_price"]) for p in points] == [
        ("import", None),
        ("shelf", None),
    ]


async def test_a_saving_a_person_marks_in_price_gives_the_regular_price(admin_client, admin):
    loc, product = await _setup(admin_client)
    pid = await make_receipt_purchase(admin.id, loc["id"], _lines([], in_price=False), total="2.99")
    body = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    item, saving = sorted(body["lines"], key=lambda x: x["seq"])
    r = await admin_client.patch(
        f"/api/v1/purchases/{pid}/lines/{saving['id']}", json={"in_price": True}
    )
    assert r.status_code == 200, r.text
    await admin_client.post(
        f"/api/v1/purchases/{pid}/lines/{item['id']}/resolve", json={"product_id": product["id"]}
    )
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).status_code == 200
    (point,) = (await _history(admin_client, product))["points"]
    assert (point["price"], point["regular_price"]) == ("2.9900", "3.4900")


async def test_cheapest_and_compare_stay_on_what_was_paid(admin_client, admin):
    """Paid 2.99 (regular 3.49) beats a 3.20 shelf price: cheapest reads what was paid."""
    loc, product = await _setup(admin_client)
    other = await make_location(admin_client, "Wrenfield Market", "Wrenfield Market")
    recent = datetime.now(UTC) - timedelta(hours=2)
    await _commit(
        admin_client, admin, loc, product, _lines(["saving_in_price"]), purchased_at=recent
    )
    await shelf(admin_client, product["id"], other["id"], "3.20")
    iid = product["ingredient"]["id"]
    out = (
        await admin_client.post("/api/v1/price-book/compare", json={"ingredient_ids": [iid]})
    ).json()
    cells = out["rows"][0]["cells"]
    sale, plain = cells[loc["vendor"]["id"]], cells[other["vendor"]["id"]]
    assert (sale["cheapest"], sale["is_promo"], sale["regular_price"]) == (True, True, "3.4900")
    assert sale["display_unit_price"] == "2.99"
    assert sale["regular_display_unit_price"] == "3.49"
    assert (plain["cheapest"], plain["regular_price"]) == (False, None)
    offers = (await admin_client.get(f"/api/v1/ingredients/{iid}/offers")).json()["items"]
    assert [(o["price"], o["regular_price"]) for o in offers] == [
        ("2.9900", "3.4900"),
        ("3.2000", None),
    ]
    history = (await admin_client.get(f"/api/v1/ingredients/{iid}/price-history")).json()
    (point,) = history["points"]  # one day, its cheapest: what was paid
    assert (point["display_unit_price"], point["regular_display_unit_price"]) == ("2.99", "3.49")
    assert history["display_low"] == "2.99"
    assert history["display_high"] == "3.20"
