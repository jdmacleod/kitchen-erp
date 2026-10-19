"""What a new product starts with: its kind, and the produce defaults (03 §1H, issue 371).

Every create path comes here: the product form, a receipt line made into a product,
To identify's naming, and a clip or lookup accepted as a new product.

The kind, when the person chose none: a store's own brand (16, 2R) is private label,
a product sold only at one vendor is that vendor's, a brand or a barcode is branded,
and anything else is loose.

Produce (an ingredient whose category key is ``produce``, ``categories.is_produce``)
is usually sold loose, by the pound or the piece, and has no brand. Stores still put
one on it: their own name, a department header ("WET VEGETABLES - …") or a word like
"Organic". So for produce:

- a brand that is one of those is not kept, whoever gave it; a brand from a page or
  a helper is not kept either, only one the person typed;
- with no brand and no barcode the kind is ``random_weight`` when it is priced per
  weight, else ``loose``;
- the name drops a leading "Fresh" and a trailing ", 1 ct" (``product_names``);
- "Organic" in the name or the brand ("Organic bananas", a store's organic line)
  marks the product organic in its attributes, even when the brand is dropped;
- with no pack given, a name that says "bunch" ("Cilantro bunch") makes the pack
  "1 bunch", unless it was priced per weight; and with a pack in bunches the name
  drops the word, since the pack says it (issue 371).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories
from app.catalog import proposals as merging
from app.catalog.brand_names import is_never_brand, name_key
from app.catalog.product_names import drop_bunch, produce_name, says_bunch
from app.models import Ingredient, PurchaseLine
from app.models.geo import Vendor
from app.schemas.catalog import ProductCreate
from app.services import brands
from app.units.convert import BUNCH
from app.units.table import units_by_code

# A department or aisle header in capitals before a dash: "WET VEGETABLES - Herbs".
_DEPARTMENT = re.compile(r"^[A-Z][A-Z&/' ]*[A-Z]\s*[-–:]")
_ORGANIC = re.compile(r"\borganics?\b", re.IGNORECASE)
_UNITS = units_by_code()


@dataclass(frozen=True)
class Start:
    name: str
    brand: str | None
    kind: str
    pack_qty: Decimal | None = None
    pack_unit: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def is_produce(ingredient: Ingredient) -> bool:
    return categories.is_produce(ingredient.category, ingredient.name)


async def store_names(db: AsyncSession) -> set[str]:
    """Every vendor's name, as brand keys: a store's name is never a produce brand."""
    return {name_key(n) for n in (await db.execute(select(Vendor.name))).scalars()}


def not_a_brand(brand: str, stores: set[str]) -> bool:
    """A store's name, a department header or a word that describes the food."""
    return is_never_brand(brand) or bool(_DEPARTMENT.match(brand)) or name_key(brand) in stores


async def produce_brand(db: AsyncSession, brand: str | None) -> str | None:
    """A produce product's brand: the one given, unless it is not a brand."""
    brand = (brand or "").strip() or None
    if brand is None or not_a_brand(brand, await store_names(db)):
        return None
    return brand


async def kind_for(
    db: AsyncSession,
    brand: str | None,
    barcode: str | None,
    *,
    exclusive_vendor_id: uuid.UUID | None = None,
    produce: bool = False,
    per_weight: bool = False,
) -> str:
    """The kind a product starts with when the person chose none."""
    if await brands.is_house_brand(db, brand):
        return "private_label"
    if exclusive_vendor_id is not None:
        return "unbranded_vendor"
    if (brand or "").strip() or barcode:
        return "branded"
    return "random_weight" if produce and per_weight else "loose"


async def start(
    db: AsyncSession,
    ingredient: Ingredient,
    *,
    name: str,
    brand: str | None,
    kind: str | None,
    barcode: str | None = None,
    exclusive_vendor_id: uuid.UUID | None = None,
    per_weight: bool = False,
    pack: tuple[Decimal | None, str | None] = (None, None),
) -> Start:
    """A new product's name, brand, kind and pack; the produce defaults when it is produce."""
    produce = is_produce(ingredient)
    pack_qty, pack_unit = pack
    attributes: dict[str, Any] = {}
    if produce:
        if _ORGANIC.search(name) or _ORGANIC.search(brand or ""):
            attributes["organic"] = True
        name = produce_name(name)
        brand = await produce_brand(db, brand)
        if pack_qty is None and not per_weight and says_bunch(name):
            pack_qty, pack_unit = Decimal(1), BUNCH
    if pack_unit == BUNCH:
        name = drop_bunch(name)
    kind = kind or await kind_for(
        db,
        brand,
        barcode,
        exclusive_vendor_id=exclusive_vendor_id,
        produce=produce,
        per_weight=per_weight,
    )
    return Start(name.strip(), brand, kind, pack_qty, pack_unit, attributes)


async def for_create(
    db: AsyncSession, ingredient: Ingredient, payload: ProductCreate, *, per_weight: bool
) -> Start:
    """How a product made from a ``ProductCreate`` starts: the form, a receipt line, naming."""
    return await start(
        db,
        ingredient,
        name=payload.name,
        brand=payload.brand,
        kind=payload.kind,
        barcode=payload.barcode,
        exclusive_vendor_id=payload.exclusive_vendor_id,
        per_weight=per_weight,
        pack=(payload.pack_qty, payload.pack_unit),
    )


async def lines_per_weight(db: AsyncSession, line_ids: list[uuid.UUID]) -> bool:
    """Whether a receipt line (any of them) was bought by weight: "0.82 lb @ 1.99 /lb"."""
    if not line_ids:
        return False
    units = await db.execute(select(PurchaseLine.unit).where(PurchaseLine.id.in_(line_ids)))
    return any(priced_per_weight({"unit": u}) for u in units.scalars())


def priced_per_weight(price: Mapping[str, Any] | None) -> bool:
    """Whether a posted price ({amount, qty, unit}) is for a weight: "0.69 / lb"."""
    unit = _UNITS.get(str((price or {}).get("unit") or ""))
    return unit is not None and unit.dimension == "mass"


async def from_fields(
    db: AsyncSession,
    ingredient: Ingredient,
    fields: dict[str, Any],
    *,
    kind: str | None,
    price: Mapping[str, Any] | None,
) -> tuple[Start, dict[str, Any]]:
    """A proposal accepted as a new product: how it starts, and the fields to apply.

    For produce, only a brand the person typed is kept (a page's or a helper's is the
    store's word, not a maker's), and the name loses its retail filler.
    """
    brand_state = fields.get("brand")
    brand = brand_state["value"] if brand_state else None
    if is_produce(ingredient) and brand_state and brand_state["source"] != "person":
        brand = None
    begin = await start(
        db,
        ingredient,
        name=str(fields["title"]["value"]),
        brand=str(brand).strip() if brand else None,
        kind=kind,
        barcode=fields["gtin"]["value"] if fields.get("gtin") else None,
        per_weight=priced_per_weight(price),
        pack=_pack_of(fields),
    )
    settled = {**fields, "title": {**fields["title"], "value": begin.name}}
    if begin.brand is None:
        settled.pop("brand", None)
    if begin.pack_unit == BUNCH and not fields.get("pack"):
        # The title said "bunch", so the pack says it now, from the title's own source.
        settled["pack"] = {
            "value": {"qty": "1", "unit": BUNCH},
            "source": fields["title"]["source"],
            "alternatives": [],
            "conflict": False,
        }
    return begin, settled


def _pack_of(fields: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    """A proposal's pack as (qty, unit), or (None, None) when it has none."""
    pack = merging.value(fields, "pack")
    try:
        return Decimal(str(pack["qty"])), str(pack["unit"])
    except (KeyError, TypeError, InvalidOperation):
        return None, None
