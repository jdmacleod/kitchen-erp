"""Regular prices, worked out when read (issue 381; spec 04, 2A and 2E).

Some tills print two prices on an item's row, its regular price and what was
paid, with the saving beneath for information. Review keeps that saving at zero
with its amount in ``in_price_amount`` (2C), and the observation records what
was paid. The regular price is the item's line total plus those savings. It is
read from the purchase line each time and never stored: ``price_observation``
is append-only, and a correction in review shows up here at once.

Only a saving proven on its own row counts: one flagged ``saving_in_price``, by
the row pass or by a person marking it in review. A saving counted as already
in the price only because the receipt's total needed it
(``saving_in_price_by_total``), or a "you saved" note (``saving_already_netted``,
which never carries ``saving_in_price``), gives no regular price. Imports and
shelf, posted or manual prices have none.

What was paid stays the price: cheapest, Compare and every range are worked out
from it. A price with a regular price is a sale price (``is_promo``), as one
with any attached discount already is.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, MutableMapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import unit_display
from app.services.pricebook import unit_price

# The one flag that says a saving's own row proves it is in the item's price.
ROW_PROVEN = "saving_in_price"
# Flags that say only the receipt's totals put it there (R2: these never count).
NOT_ROW_PROVEN = ("saving_in_price_by_total",)

_SQL = text(
    """
    SELECT o.id AS observation_id, o.price,
           item.line_total + sum(s.in_price_amount) AS regular_price,
           n.canonical_qty
    FROM price_observation o
    JOIN purchase_line item ON item.id = o.purchase_line_id AND item.removed_at IS NULL
    JOIN purchase_line s ON s.parent_line_id = item.id
         AND s.line_kind = 'discount' AND s.removed_at IS NULL
         AND s.in_price_amount > 0
         AND CAST(:proven AS varchar) = ANY(s.flags)
         AND NOT (s.flags && CAST(:not_proven AS varchar[]))
    LEFT JOIN price_norm n ON n.observation_id = o.id
         AND n.status = 'ok' AND n.canonical_qty > 0
    WHERE o.id = ANY(CAST(:ids AS uuid[])) AND o.source = 'receipt'
    GROUP BY o.id, o.price, item.line_total, n.canonical_qty
    """
)


@dataclass(frozen=True)
class Regular:
    """A receipt price's regular price, and per canonical unit when it normalizes."""

    price: Decimal
    norm_unit_price: Decimal | None


async def regular_prices(
    db: AsyncSession, observation_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Regular]:
    """The regular price of each observation that has one; the rest are absent."""
    ids = list({i for i in observation_ids if i is not None})
    if not ids:
        return {}
    rows = await db.execute(
        _SQL,
        {"ids": [str(i) for i in ids], "proven": ROW_PROVEN, "not_proven": list(NOT_ROW_PROVEN)},
    )
    out: dict[uuid.UUID, Regular] = {}
    for r in rows:
        regular = Decimal(r.regular_price)
        if regular <= r.price:
            continue  # nothing above what was paid is no regular price
        norm = unit_price(regular, r.canonical_qty) if r.canonical_qty is not None else None
        out[r.observation_id] = Regular(price=regular, norm_unit_price=norm)
    return out


def apply(
    row: MutableMapping[str, Any], regular: Regular | None, mass: str | None
) -> MutableMapping[str, Any]:
    """Add ``regular_price`` and ``regular_display_unit_price`` to a decorated row.

    The display price reads in the row's own ``display_unit``; a row with a
    regular price is promotional.
    """
    row["regular_price"] = regular.price if regular else None
    shown = (
        unit_display.shown(regular.norm_unit_price, row.get("norm_unit"), mass)
        if regular is not None and row.get("display_unit_price") is not None
        else None
    )
    row["regular_display_unit_price"] = shown.price if shown else None
    if regular is not None:
        row["is_promo"] = True
    return row


async def annotate(
    db: AsyncSession,
    rows: list[MutableMapping[str, Any]],
    mass_of: Callable[[MutableMapping[str, Any]], str | None],
) -> list[MutableMapping[str, Any]]:
    """Apply each row's regular price, keyed by its ``observation_id``."""
    found = await regular_prices(db, (r["observation_id"] for r in rows))
    for r in rows:
        apply(r, found.get(r["observation_id"]), mass_of(r))
    return rows
