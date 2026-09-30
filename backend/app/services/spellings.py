"""Other spellings of an ingredient (1G): generated plurals and curated spellings.

A spelling's key (``name_norm``) is unique across every ingredient, so one
spelling reaches exactly one ingredient. The two kinds of writer treat a taken
key differently:

- A generated plural is a convenience. When another ingredient already has the
  key, the plural is skipped and logged, and ``kerp ingredients check`` lists
  it; the save that generated it still succeeds.
- A spelling a person typed or a standard-list entry names is a statement. A
  taken key is refused with ``409 alias_taken`` naming the holder.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.names import normalize_name, plural
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.models.catalog import Ingredient, IngredientAlias, IngredientRef

log = get_logger(__name__)


async def _name_holder(
    db: AsyncSession, text: str, exclude: uuid.UUID | None = None
) -> Ingredient | None:
    """An ingredient whose canonical name is ``text``, compared case-insensitively."""
    stmt = select(Ingredient).where(func.lower(Ingredient.name) == text.strip().lower())
    if exclude is not None:
        stmt = stmt.where(Ingredient.id != exclude)
    return (await db.execute(stmt)).scalars().first()


async def add_generated_spellings(db: AsyncSession, ingredient: Ingredient) -> list[str]:
    """Add the plural of ``ingredient``'s name; return the plurals skipped as taken."""
    form = plural(ingredient.name)
    if form is None:
        return []
    key = normalize_name(form)
    if not key or key == normalize_name(ingredient.name):
        return []
    holder = await _name_holder(db, form, exclude=ingredient.id)
    if holder is None:
        inserted = await db.execute(
            insert(IngredientAlias)
            .values(
                id=new_id(),
                name_norm=key,
                ingredient_id=ingredient.id,
                kind="inflection",
                source="generated",
            )
            .on_conflict_do_nothing(index_elements=["name_norm"])
            .returning(IngredientAlias.id, IngredientAlias.ingredient_id)
        )
        if inserted.first() is not None:
            return []
        # The key exists already; it is only a skip when someone else holds it.
        owner = (
            await db.execute(
                select(IngredientAlias.ingredient_id).where(IngredientAlias.name_norm == key)
            )
        ).scalar_one_or_none()
        if owner == ingredient.id:
            return []
    log.info(
        "skipped a generated spelling another ingredient has",
        extra={"ingredient_id": str(ingredient.id), "spelling": form},
    )
    return [form]


async def add_spelling(
    db: AsyncSession,
    ingredient_id: uuid.UUID,
    text: str,
    *,
    kind: str = "synonym",
    source: str = "manual",
) -> IngredientAlias | None:
    """Add a curated spelling, refusing one another ingredient already has.

    Returns ``None`` when the text is the ingredient's own name or a spelling it
    already has. Flushes but does not commit.
    """
    key = normalize_name(text)
    if not key:
        raise ApiError(422, "empty_spelling", "A spelling needs at least one letter or digit.")
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise ApiError(404, "not_found", "No such ingredient.")
    if key == normalize_name(ingredient.name):
        return None
    holder = await _name_holder(db, text, exclude=ingredient.id)
    if holder is not None:
        raise _taken(text, holder.name)
    existing = (
        await db.execute(select(IngredientAlias).where(IngredientAlias.name_norm == key))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.ingredient_id == ingredient.id:
            return None
        other = await db.get(Ingredient, existing.ingredient_id)
        raise _taken(text, other.name if other else "another ingredient")
    alias = IngredientAlias(
        name_norm=key, ingredient_id=ingredient.id, kind=kind, source=source, confirmed_count=0
    )
    db.add(alias)
    await db.flush()
    return alias


def _taken(text: str, holder: str) -> ApiError:
    return ApiError(
        409,
        "alias_taken",
        f"“{text.strip()}” is already a name or spelling of {holder}.",
        details={"holder": holder},
    )


@dataclass(frozen=True)
class SkippedPlural:
    ingredient: str
    plural: str
    held_by: str


async def skipped_plurals(db: AsyncSession) -> list[SkippedPlural]:
    """Active ingredients whose generated plural another ingredient holds."""
    ingredients = (
        (await db.execute(select(Ingredient).where(Ingredient.active).order_by(Ingredient.name)))
        .scalars()
        .all()
    )
    by_key: dict[str, str] = {normalize_name(i.name): i.name for i in ingredients}
    alias_rows = (
        await db.execute(
            select(IngredientAlias.name_norm, IngredientAlias.ingredient_id, Ingredient.name).join(
                Ingredient, Ingredient.id == IngredientAlias.ingredient_id
            )
        )
    ).all()
    aliases = {row.name_norm: (row.ingredient_id, row.name) for row in alias_rows}
    out: list[SkippedPlural] = []
    for ingredient in ingredients:
        form = plural(ingredient.name)
        if form is None:
            continue
        key = normalize_name(form)
        if key == normalize_name(ingredient.name):
            continue
        if key in aliases:
            owner_id, owner_name = aliases[key]
            if owner_id != ingredient.id:
                out.append(SkippedPlural(ingredient.name, form, owner_name))
        elif key in by_key:
            out.append(SkippedPlural(ingredient.name, form, by_key[key]))
    return out


@dataclass(frozen=True)
class VocabularyReport:
    """What ``kerp ingredients check`` prints. Read-only."""

    skipped_plurals: list[SkippedPlural]
    without_reference: list[str]


async def vocabulary_report(db: AsyncSession) -> VocabularyReport:
    referenced = select(IngredientRef.ingredient_id).where(IngredientRef.system == "fdc")
    unreferenced = (
        (
            await db.execute(
                select(Ingredient.name)
                .where(Ingredient.active, Ingredient.id.not_in(referenced))
                .order_by(Ingredient.name)
            )
        )
        .scalars()
        .all()
    )
    return VocabularyReport(await skipped_plurals(db), list(unreferenced))
