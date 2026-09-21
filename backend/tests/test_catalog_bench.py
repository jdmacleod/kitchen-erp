from decimal import Decimal

import httpx

from app.units import (
    CanonicalQty,
    ConversionContext,
    ConversionFailure,
    Measure,
    Pack,
    ProductContext,
    convert,
)
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service


async def test_bench_matches_direct_convert(admin_client: httpx.AsyncClient, db_session):
    await seed_units_via_service(db_session)
    flour = await make_ingredient(
        admin_client, "Flour", density_g_per_ml="0.593", density_source="usda"
    )
    await admin_client.post(
        f"/api/v1/ingredients/{flour['id']}/measures",
        json={"label": "scoop", "canonical_qty": "30", "source": "measured", "confirmed": True},
    )
    bag = await make_product(admin_client, flour["id"], "Flour bag", pack_qty="5", pack_unit="lb")

    async def bench(qty, unit, product_id=None):
        r = await admin_client.post(
            f"/api/v1/ingredients/{flour['id']}/convert",
            json={"qty": qty, "unit": unit, "product_id": product_id},
        )
        assert r.status_code == 200, r.text
        return r.json()

    ctx = ConversionContext(
        "g",
        density_g_per_ml=Decimal("0.593"),
        density_source="usda",
        measures=(Measure("scoop", Decimal("30"), "measured", True),),
    )
    ctx_bag = ConversionContext(
        "g",
        density_g_per_ml=Decimal("0.593"),
        density_source="usda",
        measures=ctx.measures,
        product=ProductContext(pack=Pack(Decimal("5"), "lb")),
    )
    cases = [
        (("2", "cup", None), convert(Decimal("2"), "cup", ctx)),
        (("3", "scoop", None), convert(Decimal("3"), "scoop", ctx)),
        (("2", "each", bag["id"]), convert(Decimal("2"), "each", ctx_bag)),
        (("1", "each", None), convert(Decimal("1"), "each", ctx)),
        ((None, "g", None), convert(None, "g", ctx)),
        (("1", "smidgen", None), convert(Decimal("1"), "smidgen", ctx)),
    ]
    for (qty, unit, pid), expected in cases:
        got = await bench(qty, unit, pid)
        if isinstance(expected, CanonicalQty):
            assert got["ok"] is True, got
            assert Decimal(got["qty"]) == expected.qty
            assert got["unit"] == expected.unit
            assert got["provenance"]["bridge_kind"] == expected.provenance.bridge_kind
            assert got["provenance"]["confirmed"] == expected.provenance.confirmed
            assert (
                got["provenance"]["rests_on_unconfirmed"]
                == expected.provenance.rests_on_unconfirmed
            )
        else:
            assert isinstance(expected, ConversionFailure)
            assert got["ok"] is False and got["failure_code"] == expected.code


async def test_bench_rejects_product_of_other_ingredient(admin_client, db_session):
    await seed_units_via_service(db_session)
    a = await make_ingredient(admin_client, "A")
    b = await make_ingredient(admin_client, "B")
    pb = await make_product(admin_client, b["id"], "B pack", pack_qty="1", pack_unit="lb")
    r = await admin_client.post(
        f"/api/v1/ingredients/{a['id']}/convert",
        json={"qty": "1", "unit": "each", "product_id": pb["id"]},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "product_mismatch"
