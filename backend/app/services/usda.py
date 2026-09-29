"""Optional USDA FoodData Central reference: importer and bridge suggestions.

Reads a local download (food.csv, measure_unit.csv, food_portion.csv). Nothing
here runs at costing time; it only proposes densities and named measures when
an ingredient is created.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.catalog import RefUsdaPortion
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


def read_portions(directory: Path, skipped: Counter[str] | None = None) -> Iterable[dict]:
    """Portions of the kept food types, in the reference table's shape.

    `skipped` counts rows dropped because of their shape (as opposed to a food
    type that is simply not kept), so an import can say what it left out.
    """
    skipped = Counter() if skipped is None else skipped
    foods: dict[int, tuple[str, str]] = {}
    with (directory / "food.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            food_id = _fdc_id(row.get("fdc_id"))
            if food_id is not None and row.get("data_type") in DATA_TYPES:
                foods[food_id] = (row["description"], row["data_type"])
    units: dict[str, str] = {}
    with (directory / "measure_unit.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            units[row["id"]] = row["name"]
    with (directory / "food_portion.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            fdc_id = _fdc_id(row.get("fdc_id"))
            if fdc_id is None:
                # Can never be matched to a food. The 2026-04 download ends
                # with a block of these.
                skipped["portion names no food"] += 1
                continue
            if fdc_id not in foods:
                continue
            description, data_type = foods[fdc_id]
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


async def import_portions(
    db: AsyncSession, directory: Path, skipped: Counter[str] | None = None
) -> int:
    """Replace the reference table from a FoodData Central CSV directory.

    Returns the number of portions loaded; `skipped`, if given, collects the
    reasons rows were left out.
    """
    for name in ("food.csv", "measure_unit.csv", "food_portion.csv"):
        if not (directory / name).is_file():
            raise FileNotFoundError(f"{name} not found under {directory}")
    await db.execute(delete(RefUsdaPortion))
    total = 0
    batch: list[dict] = []
    for row in read_portions(directory, skipped):
        batch.append(row)
        if len(batch) >= BATCH:
            await db.execute(RefUsdaPortion.__table__.insert(), batch)
            total += len(batch)
            batch = []
    if batch:
        await db.execute(RefUsdaPortion.__table__.insert(), batch)
        total += len(batch)
    await db.commit()
    return total


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
            SELECT fdc_id, food_description, similarity(lower(food_description), :q) AS sim
            FROM (SELECT DISTINCT fdc_id, food_description FROM ref_usda_portion) f
            WHERE lower(food_description) % :q OR lower(food_description) LIKE :like
            ORDER BY sim DESC, food_description
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
