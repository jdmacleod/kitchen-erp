"""Which prices compare with which (issue 371): like with like, never across units.

A price normalizes per the ingredient's canonical unit (g, ml or each). A price per
bunch that nothing has weighed yet normalizes per bunch instead (status
``no_bunch_weight``), and compares only with other per-bunch prices. Once a bunch
is weighed it joins the canonical unit like everything else. So:

- a list of offers puts the canonical unit's prices first, then the per-bunch ones;
- "cheapest" is decided within one unit;
- a chart or a map shows one unit: the canonical unit's prices, or, for an
  ingredient bought only by the unweighed bunch, the per-bunch ones.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.units.convert import BUNCH


async def units_of(db: AsyncSession, ingredient_id: uuid.UUID) -> list[str]:
    """The units an ingredient's prices may compare in, the canonical one first."""
    canonical = (
        await db.execute(
            text("SELECT canonical_unit FROM ingredient WHERE id = CAST(:iid AS uuid)"),
            {"iid": ingredient_id},
        )
    ).scalar_one_or_none()
    return [canonical, BUNCH] if canonical else [BUNCH]


def cheapest_by_unit(cells: Iterable[Mapping[str, Any]]) -> dict[str, Decimal]:
    """The lowest normalized price in each unit among ``cells``."""
    best: dict[str, Decimal] = {}
    for c in cells:
        unit, price = c.get("norm_unit"), c.get("norm_unit_price")
        if unit is None or price is None:
            continue
        if unit not in best or price < best[unit]:
            best[unit] = price
    return best


def one_unit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows in the canonical unit when there are any, else the per-bunch ones."""
    canonical = [r for r in rows if r.get("norm_unit") == r.get("canonical_unit")]
    return canonical or [r for r in rows if r.get("norm_unit") == BUNCH]
