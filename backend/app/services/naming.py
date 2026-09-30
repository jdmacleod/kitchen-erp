"""Naming a household's new products in bulk from the to-identify queue (04, 2I; #88).

A first receipt matches nothing, so every line needs a product and usually an
ingredient. The naming pass offers one row per waiting group, prefilled from the
line's wording, and creates only the rows a person confirmed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import standard
from app.core.errors import ApiError
from app.models import AppUser
from app.models.catalog import Ingredient
from app.schemas.catalog import IngredientCreate, ProductCreate
from app.schemas.purchases import NameProductRow
from app.services import catalog, resolution
from app.units import UnitParseFailure, parse_unit

# "2KG", "12CT", "1.5L" in one token, or "16" then "OZ" in two.
_JOINED = re.compile(r"(\d+(?:\.\d+)?)([A-Z]+)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Pack:
    qty: Decimal
    unit: str


def read_pack(words: list[str]) -> tuple[Pack | None, list[str]]:
    """The first pack size printed in the words, and the words without it.

    Only a size the unit table knows counts: "6RL" (rolls) and "5.3Z" stay in the
    name, and no pack is read from them. A bare number ("EGGS 12") is not a size.
    """
    for i, word in enumerate(words):
        joined = _JOINED.fullmatch(word)
        if joined:
            number, unit_text, width = joined.group(1), joined.group(2), 1
        elif _NUMBER.fullmatch(word) and i + 1 < len(words) and words[i + 1].isalpha():
            number, unit_text, width = word, words[i + 1], 2
        else:
            continue
        unit = parse_unit(unit_text.lower())
        if isinstance(unit, UnitParseFailure):
            continue
        try:
            qty = Decimal(number)
        except InvalidOperation:
            continue
        if qty <= 0:
            continue
        return Pack(qty, unit), words[:i] + words[i + width :]
    return None, words


def name_from_words(words: list[str]) -> str:
    """Sentence case: "RVRBND BREAD FLR" → "Rvrbnd bread flr"."""
    text = " ".join(words).lower()
    return text[:1].upper() + text[1:]


async def naming_rows(db: AsyncSession) -> list[dict]:
    """One row per waiting group, with a suggested name, ingredient and pack."""
    rows: list[dict] = []
    for group in await resolution.to_identify(db):
        norm = group["raw_text_norm"] or ""
        pack, rest = read_pack(norm.split())
        found = await catalog.ingredients_in_text(db, norm, limit=1) if norm else []
        rows.append(
            {
                "vendor": {"id": group["vendor_id"], "name": group["vendor_name"]},
                "raw_text_norm": group["raw_text_norm"],
                "line_count": group["line_count"],
                "raw_text": group["lines"][0]["raw_text"] if group["lines"] else None,
                "name": name_from_words(rest),
                "ingredient": found[0] if found else None,
                "pack_qty": pack.qty if pack else None,
                "pack_unit": pack.unit if pack else None,
            }
        )
    return rows


async def _existing_ingredient(db: AsyncSession, spec: IngredientCreate) -> uuid.UUID | None:
    """The ingredient a new or standard choice names when one already exists,
    so two rows naming the same new ingredient create it once."""
    if spec.standard_key is not None:
        entry = standard.by_key().get(spec.standard_key)
        if entry is None:
            raise ApiError(422, "unknown_standard_key", "That standard name isn't on the list.")
        found = await db.scalar(select(Ingredient.id).where(Ingredient.slug == entry.key))
        if found is not None:
            return found
        name = entry.name
    else:
        name = spec.name
    return await db.scalar(
        select(Ingredient.id).where(func.lower(Ingredient.name) == name.strip().lower())
    )


async def name_product(
    db: AsyncSession, user: AppUser, row: NameProductRow
) -> tuple[uuid.UUID, int]:
    """Create one row's product and apply it to every waiting line of its group."""
    if not await resolution.queued_line_ids(db, row.vendor_id, row.raw_text_norm):
        raise ApiError(409, "already_identified", "These lines have been identified already.")
    ingredient_id = row.ingredient_id
    ingredient = row.ingredient
    if ingredient is not None:
        ingredient_id = await _existing_ingredient(db, ingredient)
        if ingredient_id is not None:
            ingredient = None
    product = await catalog.create_product(
        db,
        ProductCreate(
            name=row.name,
            ingredient_id=ingredient_id,
            ingredient=ingredient,
            pack_qty=row.pack_qty,
            pack_unit=row.pack_unit,
        ),
    )
    applied = await resolution.apply_to_identify(
        db,
        user,
        vendor_id=row.vendor_id,
        raw_text_norm=row.raw_text_norm,
        product_id=product.id,
        ignore=False,
        line_ids=None,
    )
    return product.id, applied


async def name_products(db: AsyncSession, user: AppUser, rows: list[NameProductRow]) -> list[dict]:
    """Each confirmed row on its own: one that fails reports why, and the rest go on."""
    results: list[dict] = []
    for row in rows:
        base = {"vendor_id": row.vendor_id, "raw_text_norm": row.raw_text_norm}
        try:
            product_id, applied = await name_product(db, user, row)
        except ApiError as exc:
            await db.rollback()
            # The rollback expired everything loaded, the signed-in user included,
            # and the next row writes that user's id on the lines it identifies.
            await db.refresh(user)
            results.append(
                {
                    **base,
                    "product_id": None,
                    "applied": 0,
                    "error": {"code": exc.code, "message": exc.message},
                }
            )
            continue
        results.append({**base, "product_id": product_id, "applied": applied, "error": None})
    return results
