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
