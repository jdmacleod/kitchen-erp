"""Helpers for catalog tests. All data is invented."""

from __future__ import annotations

import csv
from pathlib import Path

import httpx


async def seed_units_via_service(db_session) -> None:
    from app.services.units import seed_units

    await seed_units(db_session)


async def make_ingredient(client: httpx.AsyncClient, name: str, **extra) -> dict:
    r = await client.post("/api/v1/ingredients", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def make_product(client: httpx.AsyncClient, ingredient_id: str, name: str, **extra) -> dict:
    r = await client.post(
        "/api/v1/products", json={"ingredient_id": ingredient_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def write_usda_fixture(directory: Path) -> None:
    """A tiny synthetic FoodData Central download: three foods, a handful of portions."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "food.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fdc_id", "data_type", "description", "food_category_id", "publication_date"])
        w.writerow(
            [1001, "sr_legacy_food", "Flour, wheat, white, all-purpose, enriched", 18, "2019-04-01"]
        )
        w.writerow([1002, "sr_legacy_food", "Garlic, raw", 11, "2019-04-01"])
        w.writerow([1003, "branded_food", "ALL PURPOSE FLOUR", 18, "2019-04-01"])
        w.writerow([1004, "foundation_food", "Milk, whole", 1, "2020-10-30"])
    with (directory / "measure_unit.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "name"])
        w.writerow([1000, "cup"])
        w.writerow([1001, "tablespoon"])
        w.writerow([1002, "clove"])
        w.writerow([9999, "undetermined"])
    with (directory / "food_portion.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "id",
                "fdc_id",
                "seq_num",
                "amount",
                "measure_unit_id",
                "portion_description",
                "modifier",
                "gram_weight",
                "data_points",
                "footnote",
                "min_year_acquired",
            ]
        )
        w.writerow([1, 1001, 1, 1, 1000, "", "", 125, "", "", ""])
        w.writerow([2, 1001, 2, 1, 1001, "", "", 7.8, "", "", ""])
        w.writerow([3, 1002, 1, 1, 1002, "", "", 3, "", "", ""])
        w.writerow([4, 1002, 2, 1, 9999, "", "teaspoon", 2.8, "", "", ""])
        w.writerow([5, 1003, 1, 1, 1000, "", "", 130, "", "", ""])  # branded: skipped
        w.writerow([6, 1004, 1, 1, 1000, "", "", 244, "", "", ""])
        w.writerow([7, 1004, 2, 1, 9999, "", "", 0, "", "", ""])  # zero grams: skipped


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def write_usda_usage_fixture(directory: Path) -> None:
    """A synthetic download with categories and FNDDS usage (1G). Invented foods and codes.

    Two onions of equally similar text: "tan" is used by all three survey foods
    (once twice, counted once), "dun" by two (one of them through an FNDDS
    ingredient code). One survey input names nothing loaded.
    """
    directory.mkdir(parents=True, exist_ok=True)
    _write(
        directory / "food.csv",
        ["fdc_id", "data_type", "description", "food_category_id", "publication_date"],
        [
            [2001, "sr_legacy_food", "Onions, dun, raw", 11, "2019-04-01"],
            [2002, "sr_legacy_food", "Onions, tan, raw", 11, "2019-04-01"],
            [2003, "foundation_food", "Garlic, raw", 11, "2020-10-30"],
            [2004, "branded_food", "ONION CRISPS", 11, "2020-10-30"],
            [3001, "survey_fndds_food", "Onion rings", 9999, "2021-10-28"],
            [3002, "survey_fndds_food", "Soup, onion", 9999, "2021-10-28"],
            [3003, "survey_fndds_food", "Salsa, invented", 9999, "2021-10-28"],
        ],
    )
    _write(
        directory / "food_category.csv",
        ["id", "code", "description"],
        [[11, "1100", "Vegetables and Vegetable Products"]],
    )
    _write(directory / "measure_unit.csv", ["id", "name"], [[1000, "cup"], [9999, "undetermined"]])
    _write(
        directory / "food_portion.csv",
        [
            "id",
            "fdc_id",
            "seq_num",
            "amount",
            "measure_unit_id",
            "portion_description",
            "modifier",
            "gram_weight",
        ],
        [
            [1, 2001, 1, 1, 1000, "", "chopped", 160],
            [2, 2002, 1, 1, 1000, "", "chopped", 160],
            [3, 2003, 1, 1, 9999, "", "clove", 3],
            [4, 3001, 1, "", 9999, "1 ring", "", 12],
        ],
    )
    _write(
        directory / "sr_legacy_food.csv",
        ["fdc_id", "NDB_number"],
        [[2001, 91001], [2002, 91002]],
    )
    _write(
        directory / "input_food.csv",
        ["id", "fdc_id", "fdc_of_input_food", "seq_num", "amount", "sr_code", "sr_description"],
        [
            [1, 3001, "", 1, 1, 91002, "Onions, tan, raw"],
            [2, 3002, "", 1, 1, 91002, "Onions, tan, raw"],
            [3, 3002, "", 2, 1, 91002, "Onions, tan, raw"],
            [4, 3002, "", 3, 1, 91001, "Onions, dun, raw"],
            [5, 3003, 2002, 1, 1, "", "Onions, tan, raw"],
            [6, 3003, "", 2, 1, 5555, "Onion, invented code"],
            [7, 3003, "", 3, 1, 7777, "Something unresolved"],
            [8, 2001, "", 1, 1, 91002, "Not a survey parent"],
        ],
    )
    _write(
        directory / "fndds_ingredient_nutrient_value.csv",
        ["ingredient code", "Ingredient description", "Nutrient code", "FDC ID"],
        [[5555, "Onion, invented code", 203, 2001], [7777, "Something unresolved", 203, ""]],
    )
