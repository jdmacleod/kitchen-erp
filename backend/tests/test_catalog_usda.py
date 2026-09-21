from pathlib import Path

import httpx

from app.services.usda import import_portions
from tests.catalog_helpers import make_ingredient, seed_units_via_service, write_usda_fixture


async def test_without_table_no_suggestions(admin_client: httpx.AsyncClient, db_session):
    await seed_units_via_service(db_session)
    r = await admin_client.get("/api/v1/usda/suggestions", params={"name": "all-purpose flour"})
    assert r.status_code == 200
    assert r.json() == {"items": [], "loaded": False}


async def test_import_and_suggest(admin_client, db_session, tmp_path: Path):
    await seed_units_via_service(db_session)
    write_usda_fixture(tmp_path / "fdc")
    n = await import_portions(db_session, tmp_path / "fdc")
    assert n == 5  # branded row and zero-gram row skipped
    r = await admin_client.get("/api/v1/usda/suggestions", params={"name": "all-purpose flour"})
    body = r.json()
    assert body["loaded"] is True
    flour = body["items"][0]
    assert "all-purpose" in flour["description"]
    assert flour["densities"], "expected at least one density suggestion"
    cup = next(d for d in flour["densities"] if d["from_portion"].startswith("1 cup"))
    assert cup["density_g_per_ml"] == "0.52834"  # 125 g / 236.5882365 ml
    garlic = (await admin_client.get("/api/v1/usda/suggestions", params={"name": "garlic"})).json()[
        "items"
    ][0]
    assert {m["label"] for m in garlic["measures"]} == {"clove"}
    assert garlic["measures"][0]["canonical_qty_g"] == "3.00000"
    assert [d["from_portion"] for d in garlic["densities"]] == ["1 teaspoon"]

    # Re-import replaces rather than duplicates.
    assert await import_portions(db_session, tmp_path / "fdc") == 5


async def test_accepted_suggestion_is_unconfirmed_until_confirmed(
    admin_client, db_session, tmp_path
):
    await seed_units_via_service(db_session)
    write_usda_fixture(tmp_path / "fdc")
    await import_portions(db_session, tmp_path / "fdc")
    s = (
        await admin_client.get("/api/v1/usda/suggestions", params={"name": "all-purpose flour"})
    ).json()["items"][0]
    density = s["densities"][0]["density_g_per_ml"]
    ing = await make_ingredient(
        admin_client, "All-purpose flour", density_g_per_ml=density, density_source="usda"
    )
    assert ing["density_source"] == "usda" and ing["density_confirmed"] is False
    r = await admin_client.post(
        f"/api/v1/ingredients/{ing['id']}/measures",
        json={"label": "cup", "canonical_qty": "125", "source": "usda"},
    )
    assert r.json()["confirmed"] is False
    confirmed = await admin_client.post(f"/api/v1/ingredients/{ing['id']}/density/confirm")
    assert confirmed.json()["density_confirmed"] is True
