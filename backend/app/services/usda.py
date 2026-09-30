"""Optional USDA FoodData Central reference: importer and bridge suggestions.

Reads a local, unzipped download: food.csv, measure_unit.csv and
food_portion.csv, plus, when present, food_category.csv for categories and
input_food.csv, sr_legacy_food.csv and fndds_ingredient_nutrient_value.csv for
usage counts (03, 1G). Nothing here runs at costing time; it only proposes
standard-list links, densities and named measures.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.catalog import FdcFood, FdcRelease, RefUsdaPortion
from app.schemas.catalog import DensitySuggestion, MeasureSuggestion, UsdaSuggestion
from app.units import UnitParseFailure, parse_unit, units_by_code

DATA_TYPES = {"foundation_food", "sr_legacy_food", "survey_fndds_food"}
BATCH = 2000
_UNITS = units_by_code()
_Q5 = Decimal("0.00001")


def _dec(value: str) -> Decimal | None:
    try:
        d = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None
    return d if d.is_finite() and d > 0 else None


def _fdc_id(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


# A survey measure's leading quantity: "1 cup", "1/2 cup", "1 1/2 cups", "0.5 oz".
_LEADING_QTY = re.compile(r"^(?:(\d+)\s+(\d+)/(\d+)|(\d+)/(\d+)|(\d*\.?\d+))\s+(\S.*)$")


def _survey_measure(description: str) -> tuple[Decimal, str] | None:
    """(amount, unit) from a survey portion's text, or None if it names no quantity.

    Only a leading number, fraction or mixed number is read; "Quantity not
    specified" and the like name no quantity and are not guessed at.
    """
    m = _LEADING_QTY.match(description.strip())
    if m is None:
        return None
    whole, num, den, fnum, fden, number, unit = m.groups()
    if whole is not None:
        amount = Decimal(whole) + Decimal(num) / Decimal(den) if int(den) else None
    elif fnum is not None:
        amount = Decimal(fnum) / Decimal(fden) if int(fden) else None
    else:
        amount = Decimal(number)
    if amount is None or amount <= 0:
        return None
    return amount, unit.strip()


# Columns each file must have. Headers are compared lowercased and trimmed,
# because USDA has spelled a few of them differently across releases.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "food.csv": ("fdc_id", "data_type", "description"),
    "measure_unit.csv": ("id", "name"),
    "food_portion.csv": ("fdc_id", "amount", "gram_weight"),
}
# Files that add categories and usage counts. A download without one still
# imports, with that part left empty; a file that is present must be complete.
OPTIONAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "food_category.csv": ("id", "description"),
    "sr_legacy_food.csv": ("fdc_id", "ndb_number"),
    "input_food.csv": ("fdc_id", "sr_code"),
    "fndds_ingredient_nutrient_value.csv": ("ingredient code", "fdc id"),
}
_INPUT_FDC_COLUMNS = ("fdc_of_input_food", "fdc_id_of_input_food")
_RELEASE_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


class UsdaFormatError(Exception):
    """A file or column the importer needs is missing. Nothing has been written."""

    def __init__(self, file: str, column: str | None = None) -> None:
        self.file = file
        self.column = column
        what = f"{file} has no column “{column}”" if column else f"{file} is missing"
        super().__init__(what)


@dataclass
class UsdaImport:
    foods: int = 0
    portions: int = 0
    release_date: date | None = None
    absent: list[str] = field(default_factory=list)


def _rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = [h.strip().lower() for h in next(reader, [])]
        for values in reader:
            yield dict(zip(header, values, strict=False))


def _header(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return {h.strip().lower() for h in next(csv.reader(fh), [])}


def check_columns(directory: Path) -> list[str]:
    """Raise ``UsdaFormatError`` for a missing file or column; return absent optional files."""
    for name, columns in REQUIRED_COLUMNS.items():
        if not (directory / name).is_file():
            raise UsdaFormatError(name)
        header = _header(directory / name)
        for column in columns:
            if column not in header:
                raise UsdaFormatError(name, column)
    absent = []
    for name, columns in OPTIONAL_COLUMNS.items():
        if not (directory / name).is_file():
            absent.append(name)
            continue
        header = _header(directory / name)
        for column in columns:
            if column not in header:
                raise UsdaFormatError(name, column)
        if name == "input_food.csv" and not header & set(_INPUT_FDC_COLUMNS):
            raise UsdaFormatError(name, _INPUT_FDC_COLUMNS[0])
    return absent


def release_date(directory: Path) -> date | None:
    """The release date a download's directory name carries, as USDA names them."""
    m = _RELEASE_DATE.search(directory.name)
    if m is None:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


Food = tuple[str, str, str | None]  # description, data_type, category


def read_foods(directory: Path) -> dict[int, Food]:
    """The kept foods, with their category where USDA gives one (Foundation and SR)."""
    categories: dict[str, str] = {}
    if (directory / "food_category.csv").is_file():
        categories = {r["id"]: r["description"] for r in _rows(directory / "food_category.csv")}
    foods: dict[int, Food] = {}
    for row in _rows(directory / "food.csv"):
        food_id = _fdc_id(row.get("fdc_id"))
        data_type = row.get("data_type", "")
        if food_id is None or data_type not in DATA_TYPES:
            continue
        category = None
        if data_type != "survey_fndds_food":  # survey foods use WWEIA codes instead
            category = categories.get((row.get("food_category_id") or "").strip())
        foods[food_id] = (row["description"], data_type, category)
    return foods


def fndds_uses(
    directory: Path, foods: dict[int, Food], skipped: Counter[str] | None = None
) -> Counter[int]:
    """How many FNDDS survey foods use each food as an input.

    ``input_food`` names an SR input by its NDB number (``sr_code``) and leaves
    the FDC id blank, so the NDB number is resolved through ``sr_legacy_food``;
    an FNDDS-only ingredient code is resolved through the FNDDS ingredient table
    when that points at a kept food. A survey food using the same input twice
    counts once.
    """
    skipped = Counter() if skipped is None else skipped
    if not (directory / "input_food.csv").is_file():
        return Counter()
    ndb_to_fdc: dict[str, int] = {}
    if (directory / "sr_legacy_food.csv").is_file():
        for row in _rows(directory / "sr_legacy_food.csv"):
            food_id = _fdc_id(row.get("fdc_id"))
            if food_id is not None:
                ndb_to_fdc[(row.get("ndb_number") or "").strip()] = food_id
    code_to_fdc: dict[str, int] = {}
    if (directory / "fndds_ingredient_nutrient_value.csv").is_file():
        for row in _rows(directory / "fndds_ingredient_nutrient_value.csv"):
            food_id = _fdc_id(row.get("fdc id"))
            if food_id in foods:
                code_to_fdc.setdefault((row.get("ingredient code") or "").strip(), food_id)
    pairs: set[tuple[int, int]] = set()
    for row in _rows(directory / "input_food.csv"):
        parent = _fdc_id(row.get("fdc_id"))
        if parent is None or foods.get(parent, ("", "", None))[1] != "survey_fndds_food":
            continue
        food_id = next(
            (_fdc_id(row.get(c)) for c in _INPUT_FDC_COLUMNS if _fdc_id(row.get(c)) in foods),
            None,
        )
        if food_id is None:
            code = (row.get("sr_code") or "").strip()
            food_id = ndb_to_fdc.get(code) or code_to_fdc.get(code)
        if food_id is None or food_id not in foods:
            skipped["survey input names no loaded food"] += 1
            continue
        pairs.add((food_id, parent))
    return Counter(food_id for food_id, _ in pairs)


def read_portions(
    directory: Path,
    skipped: Counter[str] | None = None,
    foods: dict[int, Food] | None = None,
) -> Iterable[dict]:
    """Portions of the kept food types, in the reference table's shape.

    `skipped` counts rows dropped because of their shape (as opposed to a food
    type that is simply not kept), so an import can say what it left out.
    """
    skipped = Counter() if skipped is None else skipped
    foods = read_foods(directory) if foods is None else foods
    units: dict[str, str] = {r["id"]: r["name"] for r in _rows(directory / "measure_unit.csv")}
    for row in _rows(directory / "food_portion.csv"):
        fdc_id = _fdc_id(row.get("fdc_id"))
        if fdc_id is None:
            # Can never be matched to a food. The 2026-04 download ends
            # with a block of these.
            skipped["portion names no food"] += 1
            continue
        if fdc_id not in foods:
            continue
        description, data_type, _category = foods[fdc_id]
        grams = _dec(row.get("gram_weight", ""))
        amount = _dec(row.get("amount", ""))
        portion_text = (row.get("portion_description") or "").strip()
        if data_type == "survey_fndds_food" and amount is None:
            # Survey portions leave `amount` blank and write the whole
            # measure as text; `modifier` holds a numeric portion code.
            measure = _survey_measure(portion_text)
            if measure is None:
                skipped["survey measure has no leading quantity"] += 1
                continue
            if grams is None:
                skipped["amount or grams missing or zero"] += 1
                continue
            amount, unit = measure
            label = portion_text
        else:
            if amount is None or grams is None:
                skipped["amount or grams missing or zero"] += 1
                continue
            unit = units.get(row.get("measure_unit_id", ""), "undetermined")
            modifier = (row.get("modifier") or "").strip()
            if unit == "undetermined":
                unit = (portion_text or modifier).strip()
                if unit == modifier:
                    modifier = ""
            if not unit:
                continue
            label = " ".join(
                p for p in (format(amount, "f").rstrip("0").rstrip("."), unit, modifier) if p
            )
        yield {
            "id": new_id(),
            "fdc_id": fdc_id,
            "food_description": description,
            "portion_label": label,
            "portion_amount": amount,
            "portion_unit": unit,
            "gram_weight": grams,
            "data_type": data_type,
        }


async def _insert(db: AsyncSession, table, rows: Iterable[dict]) -> int:
    total = 0
    batch: list[dict] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= BATCH:
            await db.execute(table.insert(), batch)
            total += len(batch)
            batch = []
    if batch:
        await db.execute(table.insert(), batch)
        total += len(batch)
    return total


async def import_usda(
    db: AsyncSession, directory: Path, skipped: Counter[str] | None = None
) -> UsdaImport:
    """Replace the USDA reference tables from a FoodData Central CSV directory.

    Every file is checked before anything is written, and the foods, the
    portions and the release row are written in one transaction.
    """
    skipped = Counter() if skipped is None else skipped
    result = UsdaImport(absent=check_columns(directory), release_date=release_date(directory))
    foods = read_foods(directory)
    uses = fndds_uses(directory, foods, skipped)
    await db.execute(delete(RefUsdaPortion))
    await db.execute(delete(FdcFood))
    result.foods = await _insert(
        db,
        FdcFood.__table__,
        (
            {
                "fdc_id": food_id,
                "data_type": data_type,
                "description": description,
                "category": category,
                "fndds_uses": uses.get(food_id, 0),
            }
            for food_id, (description, data_type, category) in foods.items()
        ),
    )
    result.portions = await _insert(
        db, RefUsdaPortion.__table__, read_portions(directory, skipped, foods)
    )
    db.add(
        FdcRelease(
            release_date=result.release_date,
            source_name=directory.name,
            foods=result.foods,
            portions=result.portions,
        )
    )
    await db.commit()
    return result


async def import_portions(
    db: AsyncSession, directory: Path, skipped: Counter[str] | None = None
) -> int:
    """``import_usda``, returning the number of portions loaded (the 1C interface)."""
    return (await import_usda(db, directory, skipped)).portions


async def current_release(db: AsyncSession) -> FdcRelease | None:
    return (
        await db.execute(select(FdcRelease).order_by(FdcRelease.imported_at.desc()).limit(1))
    ).scalar_one_or_none()


async def is_loaded(db: AsyncSession) -> bool:
    return (await db.execute(select(func.count()).select_from(RefUsdaPortion))).scalar_one() > 0


def _classify(portion_unit: str) -> tuple[str, str | None]:
    """Return ("volume"|"mass"|"count"|"named", unit code or None)."""
    parsed = parse_unit(portion_unit)
    if isinstance(parsed, UnitParseFailure):
        return "named", None
    return _UNITS[parsed].dimension, parsed


async def suggest(db: AsyncSession, name: str, limit: int = 5) -> list[UsdaSuggestion]:
    """Foods whose description resembles `name`, with density and measure proposals."""
    q = name.strip().lower()
    if not q:
        return []
    rows = await db.execute(
        text(
            """
            SELECT f.fdc_id, f.food_description,
                   similarity(lower(f.food_description), :q) AS sim,
                   coalesce(u.fndds_uses, 0) AS uses
            FROM (SELECT DISTINCT fdc_id, food_description FROM ref_usda_portion) f
            LEFT JOIN fdc_food u ON u.fdc_id = f.fdc_id
            WHERE lower(f.food_description) % :q OR lower(f.food_description) LIKE :like
            -- Text similarity first, with a bonus of up to 0.2 for foods USDA's
            -- survey recipes use often, so "Onions, raw" outranks a rarely used
            -- dehydrated form of similar text (03, 1G).
            ORDER BY similarity(lower(f.food_description), :q)
                       + least(ln(1 + coalesce(u.fndds_uses, 0)), 6) / 30 DESC,
                     f.food_description
            LIMIT :limit
            """
        ),
        {"q": q, "like": f"%{q}%", "limit": limit},
    )
    foods = list(rows.mappings())
    if not foods:
        return []
    portions = (
        await db.execute(
            select(RefUsdaPortion)
            .where(RefUsdaPortion.fdc_id.in_([f["fdc_id"] for f in foods]))
            .order_by(RefUsdaPortion.portion_label)
        )
    ).scalars()
    by_food: dict[int, list[RefUsdaPortion]] = {}
    for p in portions:
        by_food.setdefault(p.fdc_id, []).append(p)
    out: list[UsdaSuggestion] = []
    for f in foods:
        densities: list[DensitySuggestion] = []
        measures: list[MeasureSuggestion] = []
        for p in by_food.get(f["fdc_id"], []):
            kind, code = _classify(p.portion_unit)
            per_unit_g = p.gram_weight / p.portion_amount
            if kind == "volume" and code is not None:
                ml = _UNITS[code].to_base_factor
                densities.append(
                    DensitySuggestion(
                        density_g_per_ml=(per_unit_g / ml).quantize(_Q5, rounding=ROUND_HALF_EVEN),
                        from_portion=p.portion_label,
                    )
                )
            elif kind in {"named", "count"}:
                label = p.portion_unit if kind == "named" else "each"
                measures.append(
                    MeasureSuggestion(
                        label=label.lower(),
                        canonical_qty_g=per_unit_g.quantize(_Q5, rounding=ROUND_HALF_EVEN),
                        from_portion=p.portion_label,
                    )
                )
        out.append(
            UsdaSuggestion(
                fdc_id=f["fdc_id"],
                description=f["food_description"],
                similarity=Decimal(str(f["sim"])).quantize(Decimal("0.001")),
                densities=densities,
                measures=measures,
            )
        )
    return out
