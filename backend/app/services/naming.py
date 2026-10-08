"""Naming a household's new products in bulk from the to-identify queue (04, 2I; #88).

A first receipt matches nothing, so every line needs a product and usually an
ingredient. The naming pass offers one row per waiting group, prefilled from the
line's wording, and creates only the rows a person confirmed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import sameness, standard
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.ingest.errors import IngestError
from app.ingest.llm import LlmClient, suggest_name
from app.models import AppUser, NamingSuggestion, Product
from app.models.catalog import Ingredient
from app.schemas.catalog import IngredientCreate, IngredientMatch, ProductCreate
from app.schemas.purchases import NameProductRow
from app.services import catalog, resolution
from app.units import UnitParseFailure, parse_unit

log = get_logger(__name__)

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
    """One row per waiting group, with a suggested name, ingredient and pack, and
    the model's suggestion when one was asked for."""
    # One load of every name and spelling serves all the groups (#183).
    index = await catalog.ingredient_index(db)
    asked = await _model_suggestions(db, index)
    rows: list[dict] = []
    for group in await resolution.to_identify(db):
        norm = group["raw_text_norm"] or ""
        pack, rest = read_pack(norm.split())
        found = await catalog.ingredients_in_text(db, norm, limit=1, index=index) if norm else []
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
                "model": asked.get((group["vendor_id"], norm)),
            }
        )
    return rows


# --- the model's suggestions (N2, N3) ------------------------------------------------


def _suggested_ingredient(
    index: catalog.IngredientIndex, suggestion: NamingSuggestion
) -> IngredientMatch | None:
    if suggestion.ingredient_id is not None:
        ingredient = index.by_id.get(suggestion.ingredient_id)
        if ingredient is None or not ingredient.active:
            return None
        return _match(ingredient)
    if suggestion.standard_key is not None:
        entry = standard.by_key().get(suggestion.standard_key)
        if entry is None:
            return None
        # Created since it was suggested: offer the catalog ingredient instead.
        taken = index.by_slug.get(entry.key)
        if taken is not None:
            return _match(taken)
        return IngredientMatch(
            kind="standard",
            key=entry.key,
            name=entry.name,
            canonical_unit=entry.unit,
            category=entry.category,
            exact=True,
        )
    return None


def _match(ingredient: Ingredient) -> IngredientMatch:
    return IngredientMatch(
        kind="ingredient",
        id=ingredient.id,
        name=ingredient.name,
        canonical_unit=ingredient.canonical_unit,
        active=ingredient.active,
        category=ingredient.category,
        exact=True,
    )


async def _model_suggestions(
    db: AsyncSession, index: catalog.IngredientIndex
) -> dict[tuple[uuid.UUID, str], dict]:
    out: dict[tuple[uuid.UUID, str], dict] = {}
    for s in (await db.execute(select(NamingSuggestion))).scalars():
        out[(s.vendor_id, s.raw_text_norm)] = {
            "status": "asking" if s.status in ("pending", "running") else s.status,
            "name": s.name,
            "ingredient": _suggested_ingredient(index, s) if s.status == "done" else None,
        }
    return out


async def request_suggestions(db: AsyncSession) -> int:
    """Ask the model about every waiting group its wording couldn't name.

    A group already asked about is asked again only if that failed; one waiting
    for its answer, or answered, is left alone. Returns how many were queued.
    """
    queued = 0
    for row in await naming_rows(db):
        model = row["model"]
        if row["ingredient"] is not None or not row["raw_text_norm"]:
            continue
        if model is not None and model["status"] != "failed":
            continue
        stmt = (
            insert(NamingSuggestion)
            .values(
                id=uuid.uuid4(),
                vendor_id=row["vendor"]["id"],
                raw_text_norm=row["raw_text_norm"],
                status="pending",
            )
            .on_conflict_do_update(
                constraint="uq_naming_suggestion_vendor_text",
                set_={
                    "status": "pending",
                    "name": None,
                    "ingredient_id": None,
                    "standard_key": None,
                    "error": None,
                    "locked_at": None,
                    "updated_at": func.now(),
                },
            )
        )
        await db.execute(stmt)
        queued += 1
    await db.commit()
    return queued


async def _claim(db: AsyncSession) -> NamingSuggestion | None:
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=get_settings().ingest_lock_timeout_seconds)
    stmt = (
        select(NamingSuggestion)
        .where(
            or_(
                NamingSuggestion.status == "pending",
                (NamingSuggestion.status == "running") & (NamingSuggestion.locked_at < stale),
            )
        )
        .order_by(NamingSuggestion.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    suggestion = (await db.execute(stmt)).scalar_one_or_none()
    if suggestion is None:
        await db.rollback()
        return None
    suggestion.status = "running"
    suggestion.locked_at = now
    await db.commit()
    return suggestion


async def _known_ingredient(
    db: AsyncSession, ingredient: str | None, product_name: str | None
) -> IngredientMatch | None:
    """The catalog or standard ingredient the model's answer names exactly, or None.

    Its ingredient first, as a whole name or spelling; failing that, one named
    inside its ingredient or product name ("Boneless chicken breast" names chicken
    breast), as for a receipt line's own wording. A near miss is never taken.
    """
    if ingredient:
        for match in await catalog.search_ingredients(
            db, ingredient, include_standard=True, limit=5
        ):
            if match.exact:
                return match
    for text in (ingredient, product_name):
        if text:
            found = await catalog.ingredients_in_text(db, text, limit=1)
            if found:
                return found[0]
    return None


async def run_suggestion_once(db: AsyncSession, *, client: LlmClient | None = None) -> bool:
    """Ask the model about one pending group. Returns False when none was waiting.

    Its answer must validate as ``LineNaming``, and its ingredient counts only when
    it is exactly the name or a spelling of a catalog ingredient or a standard-list
    entry; anything else is dropped (non-negotiable 7). Nothing is created here.
    """
    suggestion = await _claim(db)
    if suggestion is None:
        return False
    try:
        answer = await suggest_name(suggestion.raw_text_norm, client=client)
    except IngestError as exc:
        suggestion.status, suggestion.error, suggestion.locked_at = "failed", exc.code, None
        await db.commit()
        log.info("naming suggestion failed", extra={"code": exc.code})
        return True
    # Sentence case like the wording's names; the model often answers in lowercase.
    name = answer.product_name
    suggestion.name = name[:1].upper() + name[1:] if name else None
    match = await _known_ingredient(db, answer.ingredient, answer.product_name)
    if match is not None and match.kind == "ingredient":
        suggestion.ingredient_id = match.id
    elif match is not None:
        suggestion.standard_key = match.key
    suggestion.status, suggestion.locked_at = "done", None
    await db.commit()
    return True


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


def _folded(name: str) -> str:
    return " ".join(name.split()).casefold()


async def _same_product(db: AsyncSession, name: str, ingredient_id: uuid.UUID) -> Product | None:
    """An active product of the ingredient with the same name key (2P): letter case,
    spacing, trademark signs, a printed size and the product's own brand aside."""
    rows = await db.execute(
        select(Product)
        .where(Product.ingredient_id == ingredient_id, Product.active)
        .order_by(Product.created_at, Product.id)
    )
    key = sameness.name_key(name).words
    if not key:
        folded = _folded(name)
        return next((p for p in rows.scalars() if _folded(p.name) == folded), None)
    return next(
        (p for p in rows.scalars() if sameness.name_key(p.name, p.brand).words == key), None
    )


async def name_product(
    db: AsyncSession, user: AppUser, row: NameProductRow
) -> tuple[uuid.UUID, int]:
    """Create one row's product, or use the existing one it names, and apply it to
    every waiting line of its group."""
    if not await resolution.queued_line_ids(db, row.vendor_id, row.raw_text_norm):
        raise ApiError(409, "already_identified", "These lines have been identified already.")
    if row.product_id is not None:
        product = await db.get(Product, row.product_id)
        if product is None or not product.active:
            raise ApiError(409, "product_unavailable", "That product is no longer active.")
    else:
        ingredient_id = row.ingredient_id
        ingredient = row.ingredient
        if ingredient is not None:
            ingredient_id = await _existing_ingredient(db, ingredient)
            if ingredient_id is not None:
                ingredient = None
        if ingredient_id is not None and not row.allow_duplicate:
            same = await _same_product(db, row.name, ingredient_id)
            if same is not None:
                raise ApiError(
                    409,
                    "product_exists",
                    f"{same.name} already exists with this ingredient.",
                    {"product_id": str(same.id)},
                )
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
            error: dict = {"code": exc.code, "message": exc.message}
            if exc.code == "product_exists" and exc.details:
                error["product"] = await catalog.get_product(
                    db, uuid.UUID(exc.details["product_id"])
                )
            results.append({**base, "product_id": None, "applied": 0, "error": error})
            continue
        results.append({**base, "product_id": product_id, "applied": applied, "error": None})
    return results
