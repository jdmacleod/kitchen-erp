from decimal import Decimal

import asyncpg
import httpx

from app.services.units import load_units, seed_units
from app.units import SEED_UNITS


async def test_seed_is_idempotent_and_exact(db_session, owner_conn: asyncpg.Connection):
    await seed_units(db_session)
    first = await owner_conn.fetch(
        "SELECT code, to_base_factor::text AS f, aliases FROM unit ORDER BY code"
    )
    await seed_units(db_session)
    second = await owner_conn.fetch(
        "SELECT code, to_base_factor::text AS f, aliases FROM unit ORDER BY code"
    )
    assert [dict(r) for r in first] == [dict(r) for r in second]
    expected = {u.code: u for u in SEED_UNITS}
    assert {r["code"] for r in first} == set(expected)
    for row in first:
        assert Decimal(row["f"]) == expected[row["code"]].to_base_factor, row["code"]
    assert Decimal([r["f"] for r in first if r["code"] == "tsp"][0]) == Decimal("4.92892159375")


async def test_seed_removes_stray_units(db_session, owner_conn: asyncpg.Connection):
    await seed_units(db_session)
    await owner_conn.execute(
        "INSERT INTO unit (code, dimension, to_base_factor, system, aliases) "
        "VALUES ('stone', 'mass', 6350, 'us', '{}')"
    )
    await seed_units(db_session)
    assert await owner_conn.fetchval("SELECT count(*) FROM unit WHERE code = 'stone'") == 0


async def test_loaded_units_match_seed(db_session):
    await seed_units(db_session)
    loaded = await load_units(db_session)
    assert loaded == {u.code: u for u in SEED_UNITS}


async def test_units_api(admin_client: httpx.AsyncClient, db_session):
    await seed_units(db_session)
    r = await admin_client.get("/api/v1/units")
    assert r.status_code == 200
    items = {u["code"]: u for u in r.json()["items"]}
    assert items["tbsp"]["to_base_factor"] == "14.78676478125"
    assert isinstance(items["tbsp"]["to_base_factor"], str)
    p = await admin_client.get("/api/v1/units/parse", params={"text": "fluid ounces"})
    assert p.json() == {"text": "fluid ounces", "code": "fl_oz", "error": None}
    p = await admin_client.get("/api/v1/units/parse", params={"text": "smidgen"})
    assert p.json()["code"] is None and p.json()["error"] == "unknown_unit"
