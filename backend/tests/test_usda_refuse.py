"""USDA refuse: read from food_component.csv, offered as the yield (07, 3D; issue 282)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.catalog import FdcFood
from app.services.usda import import_usda
from app.services.usda_refuse import read_refuse, yield_from_refuse
from tests.catalog_helpers import (
    FOOD_COMPONENT_HEADER,
    make_ingredient,
    seed_units_via_service,
    write_usda_components,
    write_usda_fixture,
    write_usda_usage_fixture,
)

RELEASE = "FoodData_Central_csv_2026-04-30"


async def _refuse(db_session) -> dict[int, Decimal | None]:
    return {f.fdc_id: f.refuse_pct for f in (await db_session.execute(select(FdcFood))).scalars()}


async def test_import_sums_refuse_components_and_leaves_foods_without_any_null(
    db_session, tmp_path: Path
):
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    skipped: Counter[str] = Counter()
    result = await import_usda(db_session, fdc, skipped)
    assert "food_component.csv" not in result.absent
    refuse = await _refuse(db_session)
    assert refuse[2001] == Decimal("9.75")  # 6.5 + 3.25; the edible row is not refuse
    assert refuse[2002] is None  # an edible component only
    assert refuse[2003] == Decimal("12.5")
    assert refuse[3001] is None  # no component rows at all
    assert 2004 not in refuse  # branded: not kept
    assert skipped == Counter({"survey input names no loaded food": 1})


async def test_a_second_import_gives_the_same_refuse(db_session, tmp_path: Path):
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    await import_usda(db_session, fdc)
    first = await _refuse(db_session)
    await import_usda(db_session, fdc)
    db_session.expire_all()
    assert await _refuse(db_session) == first


async def test_a_download_without_the_file_imports_with_no_refuse(db_session, tmp_path: Path):
    fdc = tmp_path / "fdc"
    write_usda_fixture(fdc)
    result = await import_usda(db_session, fdc)
    assert "food_component.csv" in result.absent
    assert set((await _refuse(db_session)).values()) == {None}


@pytest.mark.parametrize("column", ["is_refuse", "pct_weight", "fdc_id"])
async def test_an_unknown_header_skips_the_file_and_says_why(
    db_session, tmp_path: Path, column: str
):
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    header = [h if h != column else f"{column}_v2" for h in FOOD_COMPONENT_HEADER]
    write_usda_components(fdc, header=header)
    skipped: Counter[str] = Counter()
    result = await import_usda(db_session, fdc, skipped)
    assert result.foods == 6  # the rest of the import goes ahead
    assert set((await _refuse(db_session)).values()) == {None}
    assert skipped[f"food_component.csv has no column “{column}”; no refuse figures read"] == 1


def test_malformed_rows_are_counted_and_left_out(tmp_path: Path):
    write_usda_components(
        tmp_path,
        rows=[
            [1, 2001, "Skin", "6.5", "maybe", "", "", ""],  # unknown flag
            [2, 2001, "Stem", "", "Y", "", "", ""],  # no percentage
            [3, 2001, "Root ends", "-2", "Y", "", "", ""],  # negative
            [4, 2002, "Shell", "60", "true", "", "", ""],
            [5, 2002, "Membrane", "40", "TRUE", "", "", ""],  # adds up to 100
            [6, 2003, "Skin", "0", "y", "", "", ""],  # a refuse part of nothing
            [7, "", "Skin", "5", "Y", "", "", ""],  # names no food
        ],
    )
    skipped: Counter[str] = Counter()
    assert read_refuse(tmp_path, {2001, 2002, 2003}, skipped) == {2003: Decimal("0.000")}
    assert skipped == Counter(
        {
            "food component's refuse flag is not yes or no": 1,
            "refuse component has no usable percentage": 2,
            "refuse adds up to 100% or more": 1,
        }
    )


def test_yield_is_100_minus_refuse_as_a_fraction():
    assert yield_from_refuse(Decimal("12.5")) == Decimal("0.875")
    assert yield_from_refuse(Decimal("9.75")) == Decimal("0.9025")
    assert yield_from_refuse(Decimal("0")) == Decimal("1")
    assert yield_from_refuse(Decimal("33.333")) == Decimal("0.6667")


async def test_the_ingredient_usda_link_carries_refuse_and_yield(
    admin_client, db_session, tmp_path: Path
):
    await seed_units_via_service(db_session)
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    await import_usda(db_session, fdc)
    ing = await make_ingredient(admin_client, "Dun onion")
    url = f"/api/v1/ingredients/{ing['id']}/usda"

    r = await admin_client.get(url)
    assert r.status_code == 200
    assert r.json() == {"reference": None}

    r = await admin_client.post(f"/api/v1/usda/review/{ing['id']}/link", json={"fdc_id": 2001})
    assert r.status_code == 200, r.text
    r = await admin_client.get(url)
    assert r.json() == {
        "reference": {
            "fdc_id": 2001,
            "description": "Onions, dun, raw",
            "refuse_pct": "9.750",
            "yield_pct": "0.9025",
        }
    }
    # Offering a yield writes nothing.
    got = await admin_client.get(f"/api/v1/ingredients/{ing['id']}")
    assert got.json()["yield_pct"] == "1.0000"

    await admin_client.post(f"/api/v1/usda/review/{ing['id']}/link", json={"fdc_id": 2002})
    r = await admin_client.get(url)
    assert r.json()["reference"] == {
        "fdc_id": 2002,
        "description": "Onions, tan, raw",
        "refuse_pct": None,
        "yield_pct": None,
    }


async def test_the_usda_link_of_an_unknown_ingredient_is_404(admin_client):
    r = await admin_client.get("/api/v1/ingredients/0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5a99/usda")
    assert r.status_code == 404
