"""Unit prices as people read them: the one formatter every price endpoint uses (issue 245).

Stored prices are per g, ml or each (non-negotiable 5). Each response that carries
``norm_unit_price`` / ``norm_unit`` also gets ``display_unit_price`` (a decimal
string) and ``display_unit`` (a label such as "lb" or "fl oz"), worked out here with
Decimal arithmetic, so the browser never converts a price.

Which weight unit an ingredient reads in is decided once per ingredient, so every
price of it in one response uses the same unit: per oz when the median weight of its
current prices' packs is under a pound, otherwise per lb (``app.units.display``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, MutableMapping
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.units import display


def system() -> display.DisplaySystem:
    return get_settings().unit_display


async def mass_units(db: AsyncSession, ingredient_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """The weight unit each ingredient's prices read in ("lb", "oz" or "kg")."""
    ids = list({i for i in ingredient_ids if i is not None})
    if not ids:
        return {}
    sys = system()
    if sys == "metric":
        return dict.fromkeys(ids, "kg")
    rows = await db.execute(
        text(
            """
            SELECT p.ingredient_id,
                   percentile_disc(0.5) WITHIN GROUP (ORDER BY pc.canonical_qty) AS usual
            FROM price_current pc
            JOIN product p ON p.id = pc.product_id
            WHERE p.ingredient_id = ANY(CAST(:ids AS uuid[]))
              AND pc.norm_unit = 'g' AND pc.canonical_qty > 0
            GROUP BY p.ingredient_id
            """
        ),
        {"ids": [str(i) for i in ids]},
    )
    usual = {r.ingredient_id: r.usual for r in rows}
    return {i: display.mass_unit(sys, _decimal(usual.get(i))) for i in ids}


async def ingredient_of_products(
    db: AsyncSession, product_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    ids = list({i for i in product_ids if i is not None})
    if not ids:
        return {}
    rows = await db.execute(
        text("SELECT id, ingredient_id FROM product WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(i) for i in ids]},
    )
    return {r.id: r.ingredient_id for r in rows}


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def shown(norm_price: Any, norm_unit: str | None, mass: str | None) -> display.DisplayPrice | None:
    return display.show(_decimal(norm_price), norm_unit, system(), mass)


def label(norm_unit: str | None, mass: str | None) -> str | None:
    """The display label for prices per ``norm_unit`` (e.g. a chart axis), or None."""
    if not norm_unit:
        return None
    code = display.display_unit(norm_unit, system(), mass or display.mass_unit(system(), None))
    return display.LABELS.get(code) if code else None


def decorate(row: MutableMapping[str, Any], mass: str | None) -> MutableMapping[str, Any]:
    """Add ``display_unit_price`` and ``display_unit`` to a row with a stored unit price."""
    out = shown(row.get("norm_unit_price"), row.get("norm_unit"), mass)
    row["display_unit_price"] = out.price if out else None
    row["display_unit"] = out.unit if out else None
    return row


async def decorate_by_product(
    db: AsyncSession, rows: list[MutableMapping[str, Any]], product_key: str = "product_id"
) -> list[MutableMapping[str, Any]]:
    """Decorate rows of mixed products, each in its own ingredient's weight unit."""
    ingredient_of = await ingredient_of_products(db, (r.get(product_key) for r in rows))
    masses = await mass_units(db, ingredient_of.values())
    for r in rows:
        decorate(r, masses.get(ingredient_of.get(r.get(product_key))))
    return rows
