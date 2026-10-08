"""Every price endpoint shows unit prices per lb, oz or fl oz by default (issue 245).

Invented vendors and products; prices chosen so the arithmetic is easy to check.
"""

import pytest

from app.core.config import get_settings
from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf


async def _setup(client):
    loc = await make_location(client, "Juniper Market", "Juniper Market Eastside", coords=SYNTH[0])
    pasta = await make_product(client, "Rigatoni", "Rigatoni 1 lb", pack_qty="1", pack_unit="lb")
    spice = await make_product(
        client, "Smoked paprika", "Smoked paprika 2 oz", pack_qty="2", pack_unit="oz"
    )
    oil = await make_product(
        client,
        "Olive oil",
        "Olive oil 16.9 fl oz",
        canonical_unit="ml",
        pack_qty="16.9",
        pack_unit="fl_oz",
    )
    await shelf(client, pasta["id"], loc["id"], "2.99")
    await shelf(client, spice["id"], loc["id"], "4.00")
    await shelf(client, oil["id"], loc["id"], "9.10")
    return loc, pasta, spice, oil


async def test_offers_read_per_pound_per_ounce_and_per_fluid_ounce(admin_client):
    _, pasta, spice, oil = await _setup(admin_client)
    seen = {}
    for p in (pasta, spice, oil):
        r = await admin_client.get(f"/api/v1/ingredients/{p['ingredient']['id']}/offers")
        assert r.status_code == 200, r.text
        item = r.json()["items"][0]
        seen[p["name"]] = (item["display_unit_price"], item["display_unit"])
        assert item["norm_unit"] in ("g", "ml")  # storage is unchanged
    assert seen == {
        "Rigatoni 1 lb": ("2.99", "lb"),
        # A pack under a pound reads per ounce.
        "Smoked paprika 2 oz": ("2.00", "oz"),
        # Under a dollar: three places.
        "Olive oil 16.9 fl oz": ("0.538", "fl oz"),
    }


async def test_compare_cells_product_history_and_panel_carry_display_prices(admin_client):
    loc, pasta, spice, _ = await _setup(admin_client)
    r = await admin_client.post(
        "/api/v1/price-book/compare",
        json={"ingredient_ids": [pasta["ingredient"]["id"], spice["ingredient"]["id"]]},
    )
    assert r.status_code == 200, r.text
    cells = {row["ingredient_name"]: next(iter(row["cells"].values())) for row in r.json()["rows"]}
    assert (cells["Rigatoni"]["display_unit_price"], cells["Rigatoni"]["display_unit"]) == (
        "2.99",
        "lb",
    )
    assert cells["Smoked paprika"]["display_unit"] == "oz"

    r = await admin_client.get(f"/api/v1/products/{pasta['id']}/prices")
    latest = r.json()["latest"][0]
    point = r.json()["points"][0]
    assert (latest["display_unit_price"], latest["display_unit"]) == ("2.99", "lb")
    assert (point["display_unit_price"], point["display_unit"]) == ("2.99", "lb")

    r = await admin_client.get(f"/api/v1/vendor-locations/{loc['id']}/price-panel")
    units = {p["product_name"]: p["display_unit"] for p in r.json()["recent"]}
    assert units["Rigatoni 1 lb"] == "lb"
    assert units["Smoked paprika 2 oz"] == "oz"
    assert units["Olive oil 16.9 fl oz"] == "fl oz"


async def test_history_range_cheapest_and_a_new_shelf_price(admin_client):
    loc, pasta, _, _ = await _setup(admin_client)
    iid = pasta["ingredient"]["id"]
    h = (await admin_client.get(f"/api/v1/ingredients/{iid}/price-history")).json()
    assert (h["display_low"], h["display_high"], h["display_unit"]) == ("2.99", "2.99", "lb")
    assert h["points"][0]["display_unit"] == "lb"

    r = await admin_client.get("/api/v1/price-book/cheapest", params={"ingredient_id": iid})
    assert r.json()["display_unit"] == "lb"
    assert r.json()["items"][0]["display_unit_price"] == "2.99"

    o = await shelf(admin_client, pasta["id"], loc["id"], "3.49")
    assert (o["norm"]["display_unit_price"], o["norm"]["display_unit"]) == ("3.49", "lb")


async def test_metric_setting_reads_per_kilogram_and_litre(admin_client, monkeypatch):
    _, pasta, spice, oil = await _setup(admin_client)
    monkeypatch.setattr(get_settings(), "unit_display", "metric")
    shown = {}
    for p in (pasta, spice, oil):
        item = (
            await admin_client.get(f"/api/v1/ingredients/{p['ingredient']['id']}/offers")
        ).json()["items"][0]
        shown[p["name"]] = (item["display_unit_price"], item["display_unit"])
    assert shown == {
        "Rigatoni 1 lb": ("6.59", "kg"),
        "Smoked paprika 2 oz": ("70.55", "kg"),
        "Olive oil 16.9 fl oz": ("18.21", "L"),
    }


@pytest.mark.parametrize("pack_qty, unit", [("12", "oz"), ("16", "lb"), ("24", "lb")])
async def test_the_usual_pack_decides_pound_or_ounce(admin_client, pack_qty, unit):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market Eastside")
    p = await make_product(
        admin_client, "Rolled oats", f"Rolled oats {pack_qty} oz", pack_qty=pack_qty, pack_unit="oz"
    )
    await shelf(admin_client, p["id"], loc["id"], "3.00")
    item = (await admin_client.get(f"/api/v1/ingredients/{p['ingredient']['id']}/offers")).json()[
        "items"
    ][0]
    assert item["display_unit"] == unit
