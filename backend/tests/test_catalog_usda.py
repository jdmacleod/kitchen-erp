import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.services.usda import _survey_measure, import_portions
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


async def test_import_skips_portions_with_no_food(db_session, tmp_path: Path):
    # The 2026-04 download ends with portion rows that name no food: id,
    # sequence, amount and grams only. One of them used to abort the whole
    # import with int('').
    await seed_units_via_service(db_session)
    write_usda_fixture(tmp_path / "fdc")
    with (tmp_path / "fdc" / "food_portion.csv").open("a", newline="") as fh:
        csv.writer(fh).writerow([8, "", 1, 1, "", "", "", 50, 3, "", ""])
    skipped: Counter[str] = Counter()
    assert await import_portions(db_session, tmp_path / "fdc", skipped) == 5
    assert skipped == {"portion names no food": 1, "amount or grams missing or zero": 1}


async def test_survey_portions_take_their_quantity_from_the_description(
    admin_client, db_session, tmp_path: Path
):
    # Survey (FNDDS) portions leave `amount` blank, use the undetermined unit,
    # write the whole measure in portion_description, and put a numeric
    # portion code in `modifier`.
    await seed_units_via_service(db_session)
    fdc = tmp_path / "fdc"
    write_usda_fixture(fdc)
    with (fdc / "food.csv").open("a", newline="") as fh:
        csv.writer(fh).writerow([1005, "survey_fndds_food", "Chicken salad", 25, "2024-10-31"])
    with (fdc / "food_portion.csv").open("a", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([9, 1005, 1, "", 9999, "1 cup", "10205", 225, "", "", ""])
        w.writerow([10, 1005, 2, "", 9999, "1/2 cup", "10206", 110, "", "", ""])
        w.writerow([11, 1005, 3, "", 9999, "Quantity not specified", "90000", 150, "", "", ""])
    skipped: Counter[str] = Counter()
    assert await import_portions(db_session, fdc, skipped) == 7
    assert skipped == {
        "survey measure has no leading quantity": 1,
        "amount or grams missing or zero": 1,
    }

    r = await admin_client.get("/api/v1/usda/suggestions", params={"name": "chicken salad"})
    salad = r.json()["items"][0]
    assert salad["description"] == "Chicken salad"
    assert {d["from_portion"]: d["density_g_per_ml"] for d in salad["densities"]} == {
        "1 cup": "0.95102",  # 225 g / 236.5882365 ml
        "1/2 cup": "0.92989",  # 110 g / half of that
    }


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 cup", (Decimal(1), "cup")),
        ("1/2 cup, diced", (Decimal(1) / Decimal(2), "cup, diced")),
        ("1 1/2 cups", (Decimal(3) / Decimal(2), "cups")),
        ("0.5 oz", (Decimal("0.5"), "oz")),
        ("Quantity not specified", None),
        ("1/0 cup", None),
        ("0 cup", None),
        ("2", None),
    ],
)
def test_survey_measure_reads_only_a_leading_quantity(text, expected):
    assert _survey_measure(text) == expected
