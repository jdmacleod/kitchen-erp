import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import httpx
import pytest

from app.services.pricebook import recompute_all
from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf

D = Decimal


async def test_packaged_each_normalizes_exactly_under_the_rounding_rule(
    admin_client: httpx.AsyncClient,
):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    o = await shelf(admin_client, box["id"], loc["id"], "3.99")
    assert o["norm"]["status"] == "ok"
    assert o["norm"]["canonical_qty"] == "453.59237"
    assert o["norm"]["norm_unit"] == "g"
    expected = (D("3.99") / D("453.59237")).quantize(D("0.000001"))
    assert D(o["norm"]["norm_unit_price"]) == expected
    assert o["norm"]["bridge_kind"] == "pack"
    assert o["source"] == "shelf" and o["voided"] is False


async def test_unnormalizable_observation_is_stored_with_failure_status(admin_client):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    o = await shelf(admin_client, flour["id"], loc["id"], "0.89", qty="1", unit="cup")
    assert o["norm"]["status"] == "no_density"
    assert o["norm"]["norm_unit_price"] is None and o["norm"]["canonical_qty"] is None
    assert o["price"] == "0.8900" and o["qty"] == "1" and o["unit"] == "cup"
    listed = await admin_client.get("/api/v1/price-book/needs-bridge")
    assert [i["status"] for i in listed.json()["items"]] == ["no_density"]


async def test_density_change_recomputes_exactly_the_dependents(
    admin_client, owner_conn: asyncpg.Connection
):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    ing_id = flour["ingredient"]["id"]
    by_cup = await shelf(admin_client, flour["id"], loc["id"], "0.89", qty="1", unit="cup")
    by_lb = await shelf(admin_client, flour["id"], loc["id"], "1.49", qty="1", unit="lb")
    assert by_cup["norm"]["status"] == "no_density" and by_lb["norm"]["status"] == "ok"
    before = await owner_conn.fetchval(
        "SELECT computed_at FROM price_norm WHERE observation_id = $1",
        __import__("uuid").UUID(by_lb["id"]),
    )
    r = await admin_client.patch(
        f"/api/v1/ingredients/{ing_id}",
        json={"density_g_per_ml": "0.593", "density_source": "usda"},
    )
    assert r.status_code == 200
    cup_after = (await admin_client.get(f"/api/v1/price-observations/{by_cup['id']}")).json()
    assert cup_after["norm"]["status"] == "ok"
    assert cup_after["norm"]["bridge_kind"] == "density"
    assert D(cup_after["norm"]["canonical_qty"]) == D("236.5882365") * D("0.593")
    after = await owner_conn.fetchval(
        "SELECT computed_at FROM price_norm WHERE observation_id = $1",
        __import__("uuid").UUID(by_lb["id"]),
    )
    assert after == before, "the same-dimension observation must not be touched"
    # The needs-a-bridge list is empty once the bridge exists.
    assert (await admin_client.get("/api/v1/price-book/needs-bridge")).json()["items"] == []


async def test_canonical_unit_change_renormalizes_same_dimension_prices(admin_client):
    # A price in the old canonical unit needed no bridge, so the bridge-only
    # recompute skipped it and left a per-ml price beside per-g ones.
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    oil = await make_product(admin_client, "Olive oil", "Estate oil", canonical_unit="ml")
    o = await shelf(admin_client, oil["id"], loc["id"], "4.00", qty="500", unit="ml")
    assert o["norm"]["status"] == "ok" and o["norm"]["norm_unit"] == "ml"

    r = await admin_client.patch(
        f"/api/v1/ingredients/{oil['ingredient']['id']}", json={"canonical_unit": "g"}
    )
    assert r.status_code == 200, r.text
    after = (await admin_client.get(f"/api/v1/price-observations/{o['id']}")).json()
    assert after["norm"]["status"] == "no_density"
    assert after["norm"]["norm_unit_price"] is None
    listed = (await admin_client.get("/api/v1/price-book/needs-bridge")).json()["items"]
    assert [i["status"] for i in listed] == ["no_density"]

    r = await admin_client.patch(
        f"/api/v1/ingredients/{oil['ingredient']['id']}",
        json={"density_g_per_ml": "0.91", "density_source": "manual"},
    )
    assert r.status_code == 200, r.text
    after = (await admin_client.get(f"/api/v1/price-observations/{o['id']}")).json()
    assert after["norm"]["status"] == "ok" and after["norm"]["norm_unit"] == "g"
    assert D(after["norm"]["canonical_qty"]) == D("500") * D("0.91")


async def test_truncate_and_recompute_reproduces_all_but_computed_at(
    admin_client, db_session, owner_conn
):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    await shelf(admin_client, box["id"], loc["id"], "3.99")
    await shelf(admin_client, flour["id"], loc["id"], "0.89", qty="1", unit="cup")
    cols = (
        "observation_id, canonical_qty::text, norm_unit, norm_unit_price::text, status, "
        "bridge_kind, bridge_source, bridge_confirmed, convert_version"
    )
    before = [
        dict(r)
        for r in await owner_conn.fetch(f"SELECT {cols} FROM price_norm ORDER BY observation_id")
    ]
    assert len(before) == 2
    n = await recompute_all(db_session)
    assert n == 2
    after = [
        dict(r)
        for r in await owner_conn.fetch(f"SELECT {cols} FROM price_norm ORDER BY observation_id")
    ]
    assert before == after


async def test_void_hides_from_views_but_keeps_audit(admin_client, owner_conn):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    o = await shelf(admin_client, box["id"], loc["id"], "3.99")
    r = await admin_client.post(
        f"/api/v1/price-observations/{o['id']}/void", json={"reason": "typo"}
    )
    assert r.status_code == 200 and r.json()["voided"] is True and r.json()["void_reason"] == "typo"
    again = await admin_client.post(
        f"/api/v1/price-observations/{o['id']}/void", json={"reason": "x"}
    )
    assert again.status_code == 409
    for view in ("price_current", "offer_latest", "ingredient_offer"):
        assert await owner_conn.fetchval(f"SELECT count(*) FROM {view}") == 0
    assert await owner_conn.fetchval("SELECT count(*) FROM price_observation") == 1
    listed = (await admin_client.get("/api/v1/price-observations")).json()["items"]
    assert listed == []
    listed = (
        await admin_client.get("/api/v1/price-observations", params={"include_voided": "true"})
    ).json()["items"]
    assert [i["id"] for i in listed] == [o["id"]]


async def test_chain_scope_fans_out_and_location_scope_does_not(admin_client, owner_conn):
    chain_a = await make_location(
        admin_client,
        "Big Chain",
        "Big Chain North",
        kind="chain",
        price_scope="chain",
        coords=SYNTH[0],
    )
    chain_b = await make_location(
        admin_client, None, "Big Chain South", vendor_id=chain_a["vendor"]["id"], coords=SYNTH[1]
    )
    indie_a = await make_location(admin_client, "Indie", "Indie One", coords=SYNTH[2])
    indie_b = await make_location(
        admin_client, None, "Indie Two", vendor_id=indie_a["vendor"]["id"], coords=SYNTH[1]
    )
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    old = datetime.now(UTC) - timedelta(days=3)
    await shelf(admin_client, box["id"], chain_a["id"], "3.49", observed_at=old.isoformat())
    newest = await shelf(admin_client, box["id"], chain_b["id"], "3.99")
    await shelf(admin_client, box["id"], indie_a["id"], "4.49")
    rows = await owner_conn.fetch(
        "SELECT applies_to_location_id::text AS loc, observation_id::text AS obs, "
        "price::text AS price "
        "FROM offer_latest WHERE product_id = $1",
        __import__("uuid").UUID(box["id"]),
    )
    by_loc = {r["loc"]: (r["obs"], r["price"]) for r in rows}
    assert by_loc[chain_a["id"]] == (newest["id"], "3.9900"), (
        "chain: newest observation anywhere applies everywhere"
    )
    assert by_loc[chain_b["id"]] == (newest["id"], "3.9900")
    assert by_loc[indie_a["id"]][1] == "4.4900"
    assert indie_b["id"] not in by_loc, "location scope: no fan-out"


async def test_promo_exclusion_falls_back_to_latest_regular(admin_client, owner_conn):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    old = datetime.now(UTC) - timedelta(days=5)
    regular = await shelf(admin_client, box["id"], loc["id"], "3.99", observed_at=old.isoformat())
    promo = await shelf(admin_client, box["id"], loc["id"], "2.99", is_promo=True)
    latest = await owner_conn.fetchval("SELECT observation_id::text FROM offer_latest")
    assert latest == promo["id"]
    regular_latest = await owner_conn.fetchval(
        "SELECT observation_id::text FROM offer_latest_regular"
    )
    assert regular_latest == regular["id"]


async def test_idempotent_shelf_price(admin_client):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    h = {"Idempotency-Key": "shelf-1"}
    body = {
        "product_id": box["id"],
        "vendor_location_id": loc["id"],
        "price": "3.99",
        "unit": "each",
    }
    a = await admin_client.post("/api/v1/price-observations", json=body, headers=h)
    b = await admin_client.post("/api/v1/price-observations", json=body, headers=h)
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
    assert len((await admin_client.get("/api/v1/price-observations")).json()["items"]) == 1


async def test_unknown_unit_is_422(admin_client):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box")
    r = await admin_client.post(
        "/api/v1/price-observations",
        json={
            "product_id": box["id"],
            "vendor_location_id": loc["id"],
            "price": "1",
            "unit": "stone",
        },
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_unit"


async def test_a_second_live_observation_for_a_line_is_rejected_until_the_first_is_voided(
    admin_client, admin, owner_conn: asyncpg.Connection
):
    """Criterion 9a (04, 2A): the ``kerp_one_live_observation_per_line`` trigger of 0005."""
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime(2026, 6, 6, 17, 0, tzinfo=UTC).isoformat(),
        "lines": [{"product_id": box["id"], "qty": "1", "unit": "each", "unit_price": "3.99"}],
    }
    r = await admin_client.post("/api/v1/purchases", json=body)
    assert r.status_code == 201, r.text
    line = r.json()["lines"][0]
    first = line["observation_id"]
    assert first

    async def insert_another() -> uuid.UUID:
        obs = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO price_observation (id, product_id, vendor_location_id, "
            "purchase_line_id, observed_at, price, qty, unit, is_promo, source, entered_by) "
            "VALUES ($1, $2, $3, $4, $5, 4.25, 1, 'each', false, 'manual', $6)",
            obs,
            uuid.UUID(box["id"]),
            uuid.UUID(loc["id"]),
            uuid.UUID(line["id"]),
            datetime(2026, 6, 6, 17, 0, tzinfo=UTC),
            admin.id,
        )
        return obs

    # The owner role bypasses no trigger: a second live observation for the line is refused.
    with pytest.raises(asyncpg.UniqueViolationError, match="already has a live price observation"):
        await insert_another()
    live = await owner_conn.fetchval(
        "SELECT count(*) FROM price_observation o WHERE o.purchase_line_id = $1 AND NOT EXISTS "
        "(SELECT 1 FROM price_observation_void v WHERE v.observation_id = o.id)",
        uuid.UUID(line["id"]),
    )
    assert live == 1

    # Voiding the first makes room for exactly one more.
    r = await admin_client.post(f"/api/v1/price-observations/{first}/void", json={"reason": "typo"})
    assert r.status_code == 200 and r.json()["voided"] is True
    second = await insert_another()
    live_ids = await owner_conn.fetch(
        "SELECT o.id FROM price_observation o WHERE o.purchase_line_id = $1 AND NOT EXISTS "
        "(SELECT 1 FROM price_observation_void v WHERE v.observation_id = o.id)",
        uuid.UUID(line["id"]),
    )
    assert [row["id"] for row in live_ids] == [second]
    with pytest.raises(asyncpg.UniqueViolationError, match="already has a live price observation"):
        await insert_another()
