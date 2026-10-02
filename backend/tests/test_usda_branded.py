"""The opt-in USDA branded table (04, 2L; plan T7): latest row per GTIN, invalid codes listed.

Every brand, description and code here is invented; codes get their check digit
computed so no literal looks like a real one.
"""

from __future__ import annotations

import csv
from pathlib import Path

from app.catalog.identifiers import check_digit
from app.services import usda_branded
from app.services.usda_branded import gtin14, import_branded
from tests import geo_helpers as gh
from tests.test_usda_import import _kerp

no_network = gh.no_network


def code(body: str) -> str:
    """A valid code from its body (spaces allowed for readability)."""
    body = body.replace(" ", "")
    return body + str(check_digit(body))


UPC = code("0 4812 3000 01")  # twelve digits: UPC-A
EAN = code("5 0123 4500 002")  # thirteen digits: EAN-13
EIGHT = code("4012 345")  # eight digits: EAN-8


def write(directory: Path, branded: list[dict], foods: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    columns = [
        "fdc_id",
        "brand_owner",
        "brand_name",
        "gtin_upc",
        "branded_food_category",
        "package_weight",
        "household_serving_fulltext",
        "modified_date",
        "available_date",
    ]
    with (directory / "branded_food.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, columns)
        w.writeheader()
        for row in branded:
            w.writerow({c: row.get(c, "") for c in columns})
    with (directory / "food.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["fdc_id", "data_type", "description"])
        w.writeheader()
        for row in foods:
            w.writerow({"data_type": "branded_food", **row})
    return directory


def fixture(tmp_path: Path) -> Path:
    return write(
        tmp_path / "FoodData_Central_branded_food_csv_2026-04-30",
        branded=[
            {
                "fdc_id": "1",
                "brand_owner": "Hollow Creek Foods",
                "brand_name": "Hollow Creek",
                "gtin_upc": UPC,
                "branded_food_category": "Canned Vegetables",
                "package_weight": "15 oz/425 g",
                "available_date": "2025-01-10",
            },
            # The same code again, later: this row wins.
            {
                "fdc_id": "2",
                "brand_owner": "Hollow Creek Foods",
                "brand_name": "Hollow Creek",
                "gtin_upc": UPC,
                "branded_food_category": "Canned Vegetables",
                "package_weight": "15.5 oz/439 g",
                "available_date": "2026-02-01",
            },
            {
                "fdc_id": "3",
                "brand_owner": "Larkfield Mills",
                "gtin_upc": EAN,
                "branded_food_category": "Flours & Corn Meal",
                "package_weight": "1 kg",
                "available_date": "2026-01-05",
            },
            {
                "fdc_id": "4",
                "brand_name": "Pebble Bay",
                "gtin_upc": EIGHT,
                "household_serving_fulltext": "1 bar",
                "available_date": "2026-01-05",
            },
            {
                "fdc_id": "5",
                "brand_name": "Bad Check",
                "gtin_upc": UPC[:-1] + str((int(UPC[-1]) + 1) % 10),
            },
            {"fdc_id": "6", "brand_name": "Too Short", "gtin_upc": "12 34".replace(" ", "")},
        ],
        foods=[
            {"fdc_id": "1", "description": "CUT GREEN BEANS"},
            {"fdc_id": "2", "description": "CUT GREEN BEANS, NO SALT ADDED"},
            {"fdc_id": "3", "description": "STRONG WHITE FLOUR"},
            {"fdc_id": "4", "description": "OAT BAR"},
            {"fdc_id": "5", "description": "NOT LOADED"},
        ],
    )


def test_codes_are_zero_padded_before_their_check_digit():
    assert gtin14(UPC) == "00" + UPC
    assert gtin14(EAN) == "0" + EAN
    assert gtin14(EIGHT) == "000000" + EIGHT
    assert gtin14(UPC[:-1] + str((int(UPC[-1]) + 1) % 10)) is None
    assert gtin14("abc") is None


async def test_import_keeps_the_latest_row_per_gtin_and_lists_invalid_codes(
    db_session, tmp_path, no_network
):
    """Loading and looking up are local: the network guard is on throughout."""
    result = await import_branded(db_session, fixture(tmp_path))
    assert (result.loaded, result.duplicates, result.invalid) == (3, 1, 2)
    assert len(result.invalid_examples) == 2

    beans = await usda_branded.lookup(db_session, "00" + UPC)
    assert beans is not None
    assert (beans.fdc_id, beans.brand, beans.description) == (
        2,
        "Hollow Creek",
        "CUT GREEN BEANS, NO SALT ADDED",
    )
    assert beans.package_size == "15.5 oz/439 g" and beans.release_date.isoformat() == "2026-02-01"
    flour = await usda_branded.lookup(db_session, "0" + EAN)
    assert flour is not None and flour.brand == "Larkfield Mills"  # owner when no brand name
    bar = await usda_branded.lookup(db_session, "000000" + EIGHT)
    assert bar is not None and bar.package_size == "1 bar"
    assert await usda_branded.is_loaded(db_session)


async def test_a_second_import_replaces_the_first(db_session, tmp_path):
    await import_branded(db_session, fixture(tmp_path))
    smaller = write(
        tmp_path / "second",
        branded=[{"fdc_id": "9", "brand_name": "Pebble Bay", "gtin_upc": EAN}],
        foods=[{"fdc_id": "9", "description": "RYE FLOUR"}],
    )
    result = await import_branded(db_session, smaller)
    assert result.loaded == 1
    assert await usda_branded.lookup(db_session, "00" + UPC) is None
    assert (await usda_branded.lookup(db_session, "0" + EAN)).description == "RYE FLOUR"


async def test_without_the_table_a_lookup_is_simply_empty(db_session):
    assert not await usda_branded.is_loaded(db_session)
    assert await usda_branded.lookup(db_session, "00" + UPC) is None


async def test_cli_loads_branded_and_reports(tmp_path):
    result = _kerp("import", "usda", "--branded", "--path", str(fixture(tmp_path)))
    assert result.returncode == 0, result.stderr
    assert "imported 3 branded foods by GTIN" in result.stdout
    assert "2 code(s) are not valid GTINs" in result.stdout


async def test_cli_missing_file_is_one_line(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _kerp("import", "usda", "--branded", "--path", str(empty))
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert "branded_food.csv" in result.stderr
