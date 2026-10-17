"""Tidy the catalog: produce sold by the bunch (issue 371).

Before ``bunch`` was a unit, a bunch of herbs was saved with no pack or a pack of
"1 each", and only its name said it was a bunch. A loose produce product whose name
says "bunch" and whose pack doesn't is offered the pack "1 bunch". Its prices then
compare per bunch, and by weight once one bunch is weighed. Once the pack says it,
the product-name check offers the name without the word.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.product_names import says_bunch
from app.schemas.catalog import ProductUpdate
from app.services import catalog
from app.services.cleanup_suggestion import Suggestion, suggestion_key
from app.units.convert import BUNCH


def _pack_text(qty: Decimal | None, unit: str | None) -> str:
    if qty is None or unit is None:
        return "no pack"
    return f"{qty.normalize():f} {unit}"


def bunch_pack(p: Any, *, produce: bool) -> Suggestion | None:
    """The pack "1 bunch" for a produce product whose name says so and whose pack is
    none or one piece; None otherwise. ``p`` has id, name, pack_qty and pack_unit."""
    if not produce or not says_bunch(p.name) or p.pack_unit == BUNCH:
        return None
    one_piece = p.pack_unit == "each" and p.pack_qty == 1
    if p.pack_qty is not None and not one_piece:
        return None
    return Suggestion(
        suggestion_key("product_pack", p.id, BUNCH),
        "product_pack",
        p.id,
        p.name,
        _pack_text(p.pack_qty, p.pack_unit),
        "1 bunch",
        "its name says it is sold by the bunch",
        BUNCH,
    )


async def apply(db: AsyncSession, product_id: uuid.UUID) -> None:
    """Set the pack to one bunch; the product's prices are normalized again."""
    await catalog.update_product(
        db, product_id, ProductUpdate(pack_qty=Decimal(1), pack_unit=BUNCH)
    )
