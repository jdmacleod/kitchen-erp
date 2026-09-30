"""`kerp import usda`: foods, categories, usage counts and the release (03, 1G, 77-78)."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.catalog import FdcFood, FdcRelease, RefUsdaPortion
from app.services.spellings import vocabulary_report
from app.services.usda import UsdaFormatError, import_usda
from tests.catalog_helpers import (
    make_ingredient,
    seed_units_via_service,
    write_usda_fixture,
    write_usda_usage_fixture,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
RELEASE = "FoodData_Central_csv_2026-04-30"


async def _foods(db_session) -> dict[int, FdcFood]:
    return {f.fdc_id: f for f in (await db_session.execute(select(FdcFood))).scalars()}


async def test_import_loads_foods_categories_and_usage(db_session, tmp_path: Path):
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    skipped: Counter[str] = Counter()
    result = await import_usda(db_session, fdc, skipped)
    assert (result.foods, result.portions) == (6, 4)
    assert result.release_date == date(2026, 4, 30)
    assert result.absent == []
    assert skipped == Counter({"survey input names no loaded food": 1})
    foods = await _foods(db_session)
    assert 2004 not in foods  # branded foods are not kept
    assert foods[2002].fndds_uses == 3  # three survey foods; the repeat counts once
    assert foods[2001].fndds_uses == 2  # one by NDB number, one by FNDDS ingredient code
    assert foods[2003].fndds_uses == 0
    assert foods[2001].category == "Vegetables and Vegetable Products"
    assert foods[3001].category is None  # survey foods carry WWEIA codes, not groups
    release = (await db_session.execute(select(FdcRelease))).scalar_one()
    assert (release.release_date, release.source_name, release.foods) == (
        date(2026, 4, 30),
        RELEASE,
        6,
    )


async def test_a_second_import_replaces_the_first(db_session, tmp_path: Path):
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    await import_usda(db_session, fdc)
    await import_usda(db_session, fdc)
    assert len(await _foods(db_session)) == 6
    portions = (
        await db_session.execute(select(func.count()).select_from(RefUsdaPortion))
    ).scalar_one()
    assert portions == 4
    releases = (await db_session.execute(select(func.count()).select_from(FdcRelease))).scalar_one()
    assert releases == 2


async def test_the_1c_download_still_imports_without_usage_files(db_session, tmp_path: Path):
    fdc = tmp_path / "fdc"
    write_usda_fixture(fdc)
    result = await import_usda(db_session, fdc)
    assert result.portions == 5 and result.release_date is None
    assert "input_food.csv" in result.absent and "food_category.csv" in result.absent
    assert {f.fndds_uses for f in (await _foods(db_session)).values()} == {0}


def _drop_column(path: Path, column: str) -> None:
    with path.open(newline="") as fh:
        rows = list(csv.reader(fh))
    keep = [i for i, h in enumerate(rows[0]) if h != column]
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows([[r[i] for i in keep if i < len(r)] for r in rows])


@pytest.mark.parametrize(
    ("file", "column"),
    [
        ("food_portion.csv", "gram_weight"),
        ("sr_legacy_food.csv", "NDB_number"),
        ("input_food.csv", "fdc_of_input_food"),
    ],
)
async def test_a_missing_column_names_file_and_column_and_changes_nothing(
    db_session, tmp_path: Path, file: str, column: str
):
    good = tmp_path / RELEASE
    write_usda_usage_fixture(good)
    await import_usda(db_session, good)
    bad = tmp_path / "FoodData_Central_csv_2026-10-31"
    write_usda_usage_fixture(bad)
    _drop_column(bad / file, column)
    with pytest.raises(UsdaFormatError) as err:
        await import_usda(db_session, bad)
    assert err.value.file == file and err.value.column == column.lower()
    await db_session.rollback()
    assert len(await _foods(db_session)) == 6
    release = (await db_session.execute(select(FdcRelease))).scalar_one()
    assert release.release_date == date(2026, 4, 30)


async def test_suggestions_rank_the_more_used_food_first(admin_client, db_session, tmp_path: Path):
    """Equal text similarity: the food more survey recipes use comes first (78)."""
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    await import_usda(db_session, fdc)
    r = await admin_client.get("/api/v1/usda/suggestions", params={"name": "onions raw"})
    assert r.status_code == 200
    assert [s["fdc_id"] for s in r.json()["items"]][:2] == [2002, 2001]


def _kerp(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )


async def test_cli_imports_and_the_old_name_still_works(tmp_path: Path):
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    for command in ("usda", "usda-portions"):
        result = _kerp("import", command, "--path", str(fdc))
        assert result.returncode == 0, result.stderr
        assert "imported 6 foods and 4 portions (release 2026-04-30)" in result.stdout
        assert "skipped 1: survey input names no loaded food" in result.stdout


async def test_cli_missing_column_is_one_line_and_exits_non_zero(tmp_path: Path):
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    _drop_column(fdc / "food.csv", "description")
    result = _kerp("import", "usda", "--path", str(fdc))
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.strip() == (
        "error: food.csv has no column “description”. Nothing was imported."
    )


async def test_check_lists_references_absent_from_the_release(
    admin_client, db_session, owner_conn, tmp_path: Path
):
    from app.core.ids import new_id

    await seed_units_via_service(db_session)
    fdc = tmp_path / RELEASE
    write_usda_usage_fixture(fdc)
    await import_usda(db_session, fdc)
    ing = await make_ingredient(admin_client, "Dun onion")
    for external_id, preferred in (("2001", True), ("424242", False)):
        await owner_conn.execute(
            "INSERT INTO ingredient_ref (id, ingredient_id, system, external_id, is_preferred) "
            "VALUES ($1, $2, 'fdc', $3, $4)",
            new_id(),
            ing["id"],
            external_id,
            preferred,
        )
    report = await vocabulary_report(db_session)
    assert report.usda_loaded
    assert report.absent_references == [("Dun onion", "424242")]
