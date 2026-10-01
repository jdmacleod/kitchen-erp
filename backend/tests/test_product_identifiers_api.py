"""The barcode field over product identifiers, kinds and attributes (03, 1H).

The API keeps ``barcode``; it reads and writes the product's barcode
identifier. Codes are invented, built with computed check digits.
"""

import uuid

import asyncpg
import pytest

from app.catalog.identifiers import check_digit, gs1_ok, upce_to_upca
from tests.pricebook_helpers import make_location


def j(spaced: str) -> str:
    return spaced.replace(" ", "")


def with_check(body: str) -> str:
    return body + str(check_digit(body))


def bad_check(code: str) -> str:
    return code[:-1] + str((int(code[-1]) + 1) % 10)


UPC = with_check(j("04812 300003"))


async def product(client, name, **extra) -> dict:
    body = {"ingredient": {"name": f"{name} ingredient", "canonical_unit": "g"}, "name": name}
    r = await client.post("/api/v1/products", json={**body, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def test_barcode_round_trips_in_display_form_and_any_written_form_finds_it(admin_client):
    p = await product(admin_client, "Hash browns", barcode=UPC)
    assert p["barcode"] == UPC
    for form in (UPC, "0" + UPC, "00" + UPC):
        r = await admin_client.get("/api/v1/products", params={"barcode": form})
        assert [x["id"] for x in r.json()["items"]] == [p["id"]]
        r = await admin_client.get("/api/v1/products/search", params={"q": form})
        hits = r.json()["items"]
        assert hits[0]["id"] == p["id"] and hits[0]["match"] == "barcode"
        assert hits[0]["barcode"] == UPC


async def test_wrong_check_digit_refused_other_codes_kept(admin_client):
    body = {"ingredient": {"name": "oats", "canonical_unit": "g"}, "name": "Oats"}
    r = await admin_client.post("/api/v1/products", json={**body, "barcode": bad_check(UPC)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_gtin"
    p = await product(admin_client, "Loose apples", barcode="4011")
    assert p["barcode"] == "4011"
    r = await admin_client.get("/api/v1/products", params={"barcode": "4011"})
    assert [x["id"] for x in r.json()["items"]] == [p["id"]]


async def test_ambiguous_eight_digits_need_a_symbology(admin_client):
    code = next(
        c
        for c in (with_check(f"0{n:06d}") for n in range(1, 100000))
        if gs1_ok(c)
        and upce_to_upca(c)
        and gs1_ok(upce_to_upca(c))
        and upce_to_upca(c).zfill(14) != c.zfill(14)
    )
    body = {"ingredient": {"name": "mints", "canonical_unit": "g"}, "name": "Mints"}
    r = await admin_client.post("/api/v1/products", json={**body, "barcode": code})
    assert r.status_code == 422 and r.json()["error"]["code"] == "ambiguous_barcode"
    p = await product(admin_client, "Mints", barcode=code, barcode_symbology="upce")
    assert p["barcode"] == upce_to_upca(code)


async def test_barcode_taken_in_another_form(admin_client):
    await product(admin_client, "First", barcode=UPC)
    body = {"ingredient": {"name": "second", "canonical_unit": "g"}, "name": "Second"}
    r = await admin_client.post("/api/v1/products", json={**body, "barcode": "0" + UPC})
    assert r.status_code == 409 and r.json()["error"]["code"] == "barcode_taken"


async def test_patch_replaces_and_clear_removes(admin_client, owner_conn: asyncpg.Connection):
    p = await product(admin_client, "Oat milk", barcode=UPC)
    other = with_check(j("04812 300004"))
    r = await admin_client.patch(f"/api/v1/products/{p['id']}", json={"barcode": other})
    assert r.status_code == 200 and r.json()["barcode"] == other
    pid = uuid.UUID(p["id"])
    count = "SELECT count(*) FROM product_identifier WHERE product_id = $1"
    assert await owner_conn.fetchval(count, pid) == 1
    r = await admin_client.patch(f"/api/v1/products/{p['id']}", json={"clear_barcode": True})
    assert r.status_code == 200 and r.json()["barcode"] is None
    assert await owner_conn.fetchval(count, pid) == 0


async def test_kind_from_evidence_and_explicit(admin_client):
    loc = await make_location(admin_client, "Invented Stall", "Invented Stall", kind="stand")
    assert (await product(admin_client, "Branded", brand="Invented Brand"))["kind"] == "branded"
    assert (await product(admin_client, "Scanned", barcode=UPC))["kind"] == "branded"
    assert (await product(admin_client, "Bananas"))["kind"] == "loose"
    stall = await product(admin_client, "Berries", exclusive_vendor_id=loc["vendor"]["id"])
    assert stall["kind"] == "unbranded_vendor"
    explicit = await product(admin_client, "Pork shoulder", kind="random_weight")
    assert explicit["kind"] == "random_weight"
    r = await admin_client.patch(f"/api/v1/products/{explicit['id']}", json={"kind": "loose"})
    assert r.json()["kind"] == "loose"


async def test_attributes_validated_by_category(admin_client):
    meat = {"name": "pork shoulder", "canonical_unit": "g", "category": "meat"}
    body = {"ingredient": meat, "name": "Pork shoulder"}
    r = await admin_client.post(
        "/api/v1/products",
        json={**body, "attributes": {"species": "pork", "bone": "bone_in"}},
    )
    assert r.status_code == 201 and r.json()["attributes"] == {"species": "pork", "bone": "bone_in"}
    odd = {"ingredient_id": r.json()["ingredient"]["id"], "name": "Odd"}
    r = await admin_client.post(
        "/api/v1/products", json={**odd, "attributes": {"species": "unicorn"}}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_attributes"
    plain = {"ingredient": {"name": "apple", "canonical_unit": "each"}, "name": "Apple"}
    r = await admin_client.post("/api/v1/products", json={**plain, "attributes": {"species": "x"}})
    assert r.status_code == 422


async def test_identifier_constraints(admin_client, owner_conn: asyncpg.Connection):
    p = await product(admin_client, "Constrained")
    loc = await make_location(admin_client, "Invented Mart", "Invented Mart", kind="chain")
    pid, vid = uuid.UUID(p["id"]), uuid.UUID(loc["vendor"]["id"])
    insert = (
        "INSERT INTO product_identifier (id, product_id, scheme, value, vendor_id, source) "
        "VALUES (gen_random_uuid(), $1, $2, $3, $4, 'manual')"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(insert, pid, "vendor_sku", "061528", None)
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(insert, pid, "plu", "4011", None)
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(insert, pid, "gtin", UPC.zfill(14), vid)
    await owner_conn.execute(insert, pid, "other", "SAME", None)
    with pytest.raises(asyncpg.UniqueViolationError):  # a missing vendor counts as equal
        await owner_conn.execute(insert, pid, "other", "SAME", None)
    await owner_conn.execute(insert, pid, "plu", "4011", vid)
    with pytest.raises(asyncpg.UniqueViolationError):
        await owner_conn.execute(insert, pid, "plu", "4011", vid)


async def test_receipt_barcode_rung_matches_any_form(admin_client, admin, db_session):
    """A receipt line carrying the EAN-13 form of a product's UPC resolves by barcode."""
    from tests.resolution_helpers import make_receipt_purchase
    from tests.test_resolution import resolve

    p = await product(admin_client, "Rice", barcode=UPC)
    loc = await make_location(admin_client, "Invented Mart", "Invented Mart", kind="chain")
    spec = {"raw_text": f"0{UPC} RICE 2.49", "line_total": "2.49", "qty": "1", "unit": "each"}
    purchase = await make_receipt_purchase(admin.id, loc["id"], [spec])
    line = (await resolve(admin_client, purchase, db_session))["lines"][0]
    assert line["resolution"] == "barcode" and line["product"]["id"] == p["id"]
