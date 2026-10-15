"""Liquids measured in g: the ml switch on Needs a bridge, and the ml default (issue 358)."""

from __future__ import annotations

from app.units import is_volume
from tests.catalog_helpers import make_ingredient, make_product
from tests.pricebook_helpers import make_location, shelf


def test_is_volume():
    assert is_volume("ml") and is_volume("fl_oz") and is_volume("cup")
    assert not is_volume("g") and not is_volume("each") and not is_volume(None)
    assert not is_volume("not a unit")


async def _bridge_rows(client) -> list[dict]:
    r = await client.get("/api/v1/price-book/needs-bridge")
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_a_no_density_row_offers_ml_only_when_every_pack_is_a_volume(admin_client):
    loc = await make_location(admin_client, "Tarn Market", "Tarn Market, Quayside")
    juice = await make_ingredient(admin_client, "Tarn apple juice")
    flour = await make_ingredient(admin_client, "Tarn rye flour")
    bottle = await make_product(
        admin_client, juice["id"], "Tarn juice, bottle", pack_qty="1.5", pack_unit="l"
    )
    carton = await make_product(
        admin_client, juice["id"], "Tarn juice, carton", pack_qty="64", pack_unit="fl_oz"
    )
    sack = await make_product(
        admin_client, flour["id"], "Tarn rye, sack", pack_qty="5", pack_unit="lb"
    )
    for p in (bottle, carton):
        await shelf(admin_client, p["id"], loc["id"], "3.49")
    await shelf(admin_client, sack["id"], loc["id"], "6.00", unit="cup")

    rows = {r["product"]["id"]: r for r in await _bridge_rows(admin_client)}
    assert rows[bottle["id"]]["status"] == "no_density"
    assert rows[bottle["id"]]["switch_to_ml"] is True
    assert rows[carton["id"]]["switch_to_ml"] is True
    # Flour bought by weight and priced by the cup needs a density, not ml.
    assert rows[sack["id"]]["status"] == "no_density"
    assert rows[sack["id"]]["switch_to_ml"] is False

    # One product sold by weight is enough to keep the ingredient in g.
    await make_product(
        admin_client, juice["id"], "Tarn juice powder", pack_qty="200", pack_unit="g"
    )
    rows = {r["product"]["id"]: r for r in await _bridge_rows(admin_client)}
    assert rows[bottle["id"]]["switch_to_ml"] is False


async def test_switching_to_ml_bridges_the_prices(admin_client):
    loc = await make_location(admin_client, "Tarn Market", "Tarn Market, Quayside")
    juice = await make_ingredient(admin_client, "Tarn pear juice")
    bottle = await make_product(
        admin_client, juice["id"], "Tarn pear juice, bottle", pack_qty="1", pack_unit="l"
    )
    await shelf(admin_client, bottle["id"], loc["id"], "2.99")
    assert [r["status"] for r in await _bridge_rows(admin_client)] == ["no_density"]
    r = await admin_client.patch(
        f"/api/v1/ingredients/{juice['id']}", json={"canonical_unit": "ml"}
    )
    assert r.status_code == 200, r.text
    assert await _bridge_rows(admin_client) == []


async def test_a_unit_change_with_measures_in_the_old_unit_must_drop_them(admin_client):
    syrup = await make_ingredient(admin_client, "Tarn maple syrup")
    r = await admin_client.post(
        f"/api/v1/ingredients/{syrup['id']}/measures",
        json={"label": "bottle", "canonical_qty": "750", "source": "label"},
    )
    assert r.status_code == 201, r.text

    r = await admin_client.patch(
        f"/api/v1/ingredients/{syrup['id']}", json={"canonical_unit": "ml"}
    )
    assert r.status_code == 409
    body = r.json()["error"]
    assert body["code"] == "measures_in_old_unit"
    assert body["details"] == {
        "unit": "g",
        "measures": [{"label": "bottle", "canonical_qty": "750"}],
    }
    after = (await admin_client.get(f"/api/v1/ingredients/{syrup['id']}")).json()
    assert after["canonical_unit"] == "g" and len(after["measures"]) == 1

    r = await admin_client.patch(
        f"/api/v1/ingredients/{syrup['id']}", json={"canonical_unit": "ml", "drop_measures": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["canonical_unit"] == "ml" and r.json()["measures"] == []

    # Other edits, and a "change" to the same unit, are untouched by the guard.
    await admin_client.post(
        f"/api/v1/ingredients/{syrup['id']}/measures",
        json={"label": "bottle", "canonical_qty": "750", "source": "label"},
    )
    r = await admin_client.patch(
        f"/api/v1/ingredients/{syrup['id']}", json={"canonical_unit": "ml", "notes": "Grade A"}
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["measures"]) == 1


async def test_a_new_ingredient_for_a_volume_pack_is_measured_in_ml(admin_client):
    def body(name: str, unit: str, **ingredient) -> dict:
        return {
            "name": name,
            "pack_qty": "1",
            "pack_unit": unit,
            "allow_duplicate": True,
            "ingredient": {"name": name, "category": "drinks", "keep_name": True, **ingredient},
        }

    async def unit_of(payload: dict) -> str:
        r = await admin_client.post("/api/v1/products", json=payload)
        assert r.status_code == 201, r.text
        ingredient = await admin_client.get(f"/api/v1/ingredients/{r.json()['ingredient']['id']}")
        return ingredient.json()["canonical_unit"]

    assert await unit_of(body("Tarn cider", "l")) == "ml"
    assert await unit_of(body("Tarn oat flakes", "kg")) == "g"
    # A unit the request names is kept.
    assert await unit_of(body("Tarn honey", "fl_oz", canonical_unit="g")) == "g"
