"""USDA branded foods by barcode (04, 2L): opt-in, local, read-only at runtime.

`kerp import usda --branded --path DIR` reads a local, unzipped FoodData
Central *branded* download: ``branded_food.csv`` for the codes, brands,
categories and package sizes, and ``food.csv`` for the descriptions. Each code
is zero-padded to GTIN-14 before its check digit is tested. Where USDA lists one
code more than once, the latest row wins (by available date, then modified
date, then FDC id). Codes that are not valid GTINs are counted and listed.

The load replaces the previous one in a single transaction. Looking a barcode
up here is a database read; nothing is sent anywhere (non-negotiable 9).
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identifiers import gs1_ok
from app.models.catalog import FdcBranded
from app.services.usda import BATCH, UsdaFormatError

REQUIRED = {
    "branded_food.csv": ("fdc_id", "gtin_upc"),
    "food.csv": ("fdc_id", "description"),
}
INVALID_EXAMPLES = 20


@dataclass
class BrandedImport:
    loaded: int = 0
    duplicates: int = 0
    invalid: int = 0
    invalid_examples: list[str] = field(default_factory=list)


def _rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = [h.strip().lower() for h in next(reader, [])]
        for values in reader:
            yield dict(zip(header, values, strict=False))


def check_columns(directory: Path) -> None:
    for name, columns in REQUIRED.items():
        path = directory / name
        if not path.is_file():
            raise UsdaFormatError(name)
        with path.open(newline="", encoding="utf-8-sig") as fh:
            header = {h.strip().lower() for h in next(csv.reader(fh), [])}
        for column in columns:
            if column not in header:
                raise UsdaFormatError(name, column)


def gtin14(code: str) -> str | None:
    """The code zero-padded to fourteen digits, or None when it is not a valid GTIN."""
    digits = code.strip()
    if not digits.isdigit() or not 8 <= len(digits) <= 14:
        return None
    padded = digits.zfill(14)
    return padded if gs1_ok(padded) else None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _package(row: dict[str, str]) -> str | None:
    """USDA's own package text, as printed (``package_weight``), else the serving text."""
    return _text(row.get("package_weight")) or _text(row.get("household_serving_fulltext"))


def read_branded(directory: Path, result: BrandedImport) -> dict[str, tuple]:
    """GTIN-14 → the latest row's (sort key, fdc_id, brand, category, package, date)."""
    best: dict[str, tuple] = {}
    for row in _rows(directory / "branded_food.csv"):
        raw = row.get("gtin_upc", "")
        code = gtin14(raw)
        try:
            fdc_id = int(row.get("fdc_id", ""))
        except ValueError:
            continue
        if code is None:
            result.invalid += 1
            if len(result.invalid_examples) < INVALID_EXAMPLES:
                result.invalid_examples.append(raw.strip())
            continue
        available = _date(row.get("available_date"))
        modified = _date(row.get("modified_date"))
        key = (available or date.min, modified or date.min, fdc_id)
        brand = _text(row.get("brand_name")) or _text(row.get("brand_owner"))
        entry = (
            key,
            fdc_id,
            brand,
            _text(row.get("branded_food_category")),
            _package(row),
            available or modified,
        )
        if code in best:
            result.duplicates += 1
            if best[code][0] >= key:
                continue
        best[code] = entry
    return best


def read_descriptions(directory: Path, wanted: set[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for row in _rows(directory / "food.csv"):
        try:
            fdc_id = int(row.get("fdc_id", ""))
        except ValueError:
            continue
        if fdc_id in wanted and (description := _text(row.get("description"))):
            out[fdc_id] = description
    return out


async def import_branded(db: AsyncSession, directory: Path) -> BrandedImport:
    """Replace ``fdc_branded`` from a branded download, in one transaction."""
    check_columns(directory)
    result = BrandedImport()
    best = read_branded(directory, result)
    descriptions = read_descriptions(directory, {entry[1] for entry in best.values()})
    await db.execute(delete(FdcBranded))
    batch: list[dict] = []
    table = FdcBranded.__table__
    for code, (_, fdc_id, brand, category, package, released) in best.items():
        description = descriptions.get(fdc_id)
        if description is None:
            continue  # a branded row with no food row says nothing about the product
        batch.append(
            {
                "gtin": code,
                "fdc_id": fdc_id,
                "brand": brand,
                "description": description,
                "category": category,
                "package_size": package,
                "release_date": released,
            }
        )
        if len(batch) >= BATCH:
            await db.execute(table.insert(), batch)
            result.loaded += len(batch)
            batch = []
    if batch:
        await db.execute(table.insert(), batch)
        result.loaded += len(batch)
    await db.commit()
    return result


async def lookup(db: AsyncSession, gtin: str) -> FdcBranded | None:
    """The branded food for a GTIN-14, when the table is loaded and knows it."""
    return await db.get(FdcBranded, gtin)


async def is_loaded(db: AsyncSession) -> bool:
    return bool((await db.execute(select(func.count()).select_from(FdcBranded))).scalar_one())
