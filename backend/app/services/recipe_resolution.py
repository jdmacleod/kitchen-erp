"""Ingredient resolution for recipe lines, the resolve queue and pins (07, 3C).

The lookup (VC3): a line resolves without a person only through an ingredient's
own normalized name or one of its spellings (``ingredient_alias``), exactly, or
through an inflection of either (1G's ``singulars``). A name in
``RECIPES_NEGLIGIBLE_NAMES`` makes its line negligible; a name in
``recipe_name_ignore`` makes it ignored. Everything else stays ``unmatched``
and waits in the queue with proposals from the cascade, which are suggestions
and are never applied here.

The cascade (VS2) proposes in tiers, each one proposer in ``PROPOSERS``: an
exact standard-list entry; trigram matches; the name without its prep words
(``app/recipes/prep.py``), whose stripped words a decision moves into the
line's note; foods from the USDA pool to create an ingredient from; and, only
when a person asks about one name (:func:`ask`), the local model's pick from a
shortlist it cannot leave. Three proposals per name, higher tiers first.

A decision applies to every unmatched line with that name across all recipes
(criterion 15) and writes its alias through the 1G spelling service, never the
receipt alias writer: ``services/resolution.py`` is the receipt side and is not
touched (criterion 18). After a decision every remaining unmatched line is
looked up again, so a new ingredient's own name settles other names at once.

Pins (criterion 17) live in ``recipe_pin`` keyed by the recipe and the name as
normalized, so they survive re-indexing while the name in the file is unchanged.

The lookup lives in ``services/recipe_lookup``, the cascade's tiers in
``services/recipe_proposals`` and pins in ``services/recipe_pins``; the names the
router and the tests use are re-exported here.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Text, case, distinct, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.models import (
    AppUser,
    Ingredient,
    IngredientAlias,
    Recipe,
    RecipeIngredient,
    RecipeNameIgnore,
)
from app.models.catalog import FdcFood
from app.schemas.catalog import IngredientCreate, IngredientSummary
from app.schemas.recipes import (
    ResolveAskOut,
    ResolveDecisionIn,
    ResolveDecisionOut,
    ResolveName,
    ResolveQueueOut,
    ResolveRecipe,
)
from app.services import recipe_cost_triggers
from app.services.catalog import new_ingredient, set_preferred_fdc
from app.services.recipe_lookup import Context as Context
from app.services.recipe_lookup import apply_lookup, load_context
from app.services.recipe_lookup import lookup as lookup
from app.services.recipe_pins import delete_pin as delete_pin
from app.services.recipe_pins import pins_out as pins_out
from app.services.recipe_pins import set_pin as set_pin
from app.services.recipe_proposals import MAX_PROPOSALS, proposals_for
from app.services.recipe_proposals import TIER_ORDER as TIER_ORDER
from app.services.spellings import add_spelling

log = get_logger(__name__)

RAW_NAME_SAMPLES = 5

# A line the queue shows and counts: unmatched, not negligible, and of a recipe
# whose file is still there. A missing recipe's lines are its last good parse
# of a file that is gone; they are neither asked about nor counted, and a
# decision still covers them, so a relinked recipe comes back resolved.
_QUEUED = (
    RecipeIngredient.resolution == "unmatched",
    RecipeIngredient.negligible.is_(False),
    Recipe.status != "missing",
)


async def resolve_unmatched(db: AsyncSession, settings: Settings | None = None) -> int:
    """Look every unmatched line up again; flushes and does not commit.

    Run after a scan (new rows are written unmatched, so this covers rows just
    rebuilt and rows that waited) and after a decision (criterion 14). Returns
    how many lines stopped being unmatched.
    """
    rows = (
        (
            await db.execute(
                select(RecipeIngredient).where(RecipeIngredient.resolution == "unmatched")
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    ctx = await load_context(db, settings)
    settled = sum(apply_lookup(ctx, row) for row in rows)
    await db.flush()
    return settled


# --- the queue -------------------------------------------------------------------------


async def queue_counts(db: AsyncSession) -> tuple[int, int, datetime | None]:
    """Names waiting, the recipes they touch, and the oldest such recipe (the inbox row)."""
    names, recipes, oldest = (
        await db.execute(
            select(
                func.count(distinct(RecipeIngredient.name_norm)),
                func.count(distinct(RecipeIngredient.recipe_id)),
                func.min(Recipe.created_at),
            )
            .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
            .where(*_QUEUED)
        )
    ).one()
    return names, recipes, oldest


@dataclass
class _Group:
    raw_names: Counter[str]
    recipes: dict[uuid.UUID, ResolveRecipe]
    lines: int = 0


async def queue(db: AsyncSession, settings: Settings | None = None) -> ResolveQueueOut:
    """Unmatched names grouped by normalized name, most-used first, with proposals."""
    rows = await db.execute(
        select(
            RecipeIngredient.name_norm,
            RecipeIngredient.raw_name,
            Recipe.id,
            Recipe.title,
            Recipe.path,
        )
        .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
        .where(*_QUEUED)
    )
    groups: dict[str, _Group] = {}
    for name_norm, raw_name, recipe_id, title, path in rows:
        group = groups.setdefault(name_norm, _Group(Counter(), {}))
        group.raw_names[raw_name] += 1
        group.recipes.setdefault(recipe_id, ResolveRecipe(id=recipe_id, title=title, path=path))
        group.lines += 1
    if not groups:
        return ResolveQueueOut(items=[], names=0, recipes=0)
    ordered = sorted(
        groups.items(), key=lambda item: (-item[1].lines, -len(item[1].recipes), item[0])
    )
    ctx = await load_context(db, settings)
    items: list[ResolveName] = []
    for name_norm, group in ordered:
        items.append(
            ResolveName(
                name_norm=name_norm,
                raw_names=[raw for raw, _ in group.raw_names.most_common(RAW_NAME_SAMPLES)],
                recipes=sorted(group.recipes.values(), key=lambda r: (r.title.casefold(), r.path)),
                line_count=group.lines,
                proposals=await proposals_for(db, ctx, name_norm),
            )
        )
    touched = {recipe_id for group in groups.values() for recipe_id in group.recipes}
    return ResolveQueueOut(items=items, names=len(items), recipes=len(touched))


async def ask(db: AsyncSession, name_norm: str, settings: Settings | None = None) -> ResolveAskOut:
    """The cascade for one queued name with the model tier allowed to run.

    The queue listing leaves the model out, because a call per name would hold
    the page for minutes; a person asks about one name at a time. The answer is
    the same capped, ordered list the queue shows, with the model's picks where
    the earlier tiers left room. Nothing is applied (VC3).
    """
    name_norm = name_norm.strip()
    queued = (
        await db.execute(
            select(func.count())
            .select_from(RecipeIngredient)
            .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
            .where(RecipeIngredient.name_norm == name_norm, *_QUEUED)
        )
    ).scalar_one()
    if not queued:
        raise ApiError(404, "not_found", "No unresolved recipe line has that name.")
    configured = bool((settings or get_settings()).llm_model)
    ctx = await load_context(db, settings, ask_model=configured)
    proposals = await proposals_for(db, ctx, name_norm)
    asked = ctx.ask_model and sum(p.tier != "model" for p in proposals) < MAX_PROPOSALS
    return ResolveAskOut(name_norm=name_norm, proposals=proposals, model_asked=asked)


# --- decisions -------------------------------------------------------------------------


async def _holder(db: AsyncSession, name_norm: str, exclude: uuid.UUID) -> Ingredient | None:
    """The other ingredient that has ``name_norm`` as a spelling or as its name."""
    by_spelling = (
        (
            await db.execute(
                select(Ingredient)
                .join(IngredientAlias, IngredientAlias.ingredient_id == Ingredient.id)
                .where(IngredientAlias.name_norm == name_norm, Ingredient.id != exclude)
            )
        )
        .scalars()
        .first()
    )
    if by_spelling is not None:
        return by_spelling
    return (
        (
            await db.execute(
                select(Ingredient).where(
                    func.lower(Ingredient.name) == name_norm, Ingredient.id != exclude
                )
            )
        )
        .scalars()
        .first()
    )


async def _learn(db: AsyncSession, ingredient: Ingredient, name_norm: str) -> None:
    """Write the name as the ingredient's spelling (kind synonym, source recipe).

    A name another ingredient has is refused with ``409 alias_taken`` naming the
    holder and its id, so the page can offer "Use {holder}". Confirming a name
    the ingredient already has bumps that spelling's count.
    """
    try:
        alias = await add_spelling(db, ingredient.id, name_norm, kind="synonym", source="recipe")
    except ApiError as exc:
        if exc.code != "alias_taken":
            raise
        holder = await _holder(db, name_norm, exclude=ingredient.id)
        details = dict(exc.details or {})
        if holder is not None:
            details["holder_id"] = str(holder.id)
            details["holder"] = holder.name
        raise ApiError(409, "alias_taken", exc.message, details=details) from None
    now = datetime.now(UTC)
    if alias is None:
        alias = (
            await db.execute(
                select(IngredientAlias).where(
                    IngredientAlias.name_norm == name_norm,
                    IngredientAlias.ingredient_id == ingredient.id,
                )
            )
        ).scalar_one_or_none()
        if alias is None:
            return  # the ingredient's own name: nothing to count on
        alias.confirmed_count += 1
    else:
        alias.confirmed_count = 1
    alias.last_seen_at = now
    await db.flush()


async def _create(db: AsyncSession, spec: IngredientCreate) -> Ingredient:
    """Create the ingredient inline, flushed; a taken name names its holder."""
    taken = (
        (
            await db.execute(
                select(Ingredient).where(func.lower(Ingredient.name) == spec.name.strip().lower())
            )
        )
        .scalars()
        .first()
    )
    if taken is not None:
        raise ApiError(
            409,
            "ingredient_name_taken",
            f"An ingredient named {taken.name} exists already.",
            details={"holder": taken.name, "holder_id": str(taken.id)},
        )
    try:
        async with db.begin_nested():
            return await new_ingredient(db, spec)
    except IntegrityError as exc:
        raise ApiError(
            409, "ingredient_name_taken", "An ingredient with that name or standard key exists."
        ) from exc


async def _chosen(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient:
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise ApiError(404, "not_found", "No such ingredient.")
    if not ingredient.active:
        raise ApiError(
            409,
            "ingredient_inactive",
            "That ingredient is not active.",
            details={"merged_into": str(ingredient.merged_into)}
            if ingredient.merged_into
            else None,
        )
    return ingredient


async def decide(db: AsyncSession, user: AppUser, payload: ResolveDecisionIn) -> ResolveDecisionOut:
    """One decision for a name, applied to every unmatched line that has it."""
    name_norm = payload.name_norm.strip()
    waiting = (
        await db.execute(
            select(func.count(), func.count(distinct(RecipeIngredient.recipe_id))).where(
                RecipeIngredient.name_norm == name_norm,
                RecipeIngredient.resolution == "unmatched",
            )
        )
    ).one()
    lines, recipes = waiting
    if not lines:
        raise ApiError(404, "not_found", "No unresolved recipe line has that name.")
    pending = await recipe_cost_triggers.unmatched_lines(db)
    unmatched = (
        update(RecipeIngredient)
        .where(RecipeIngredient.name_norm == name_norm, RecipeIngredient.resolution == "unmatched")
        .execution_options(synchronize_session=False)
    )
    ingredient: Ingredient | None = None
    if payload.ignore:
        action = "ignored"
        await db.execute(
            insert(RecipeNameIgnore)
            .values(id=new_id(), name_norm=name_norm, created_by=user.id)
            .on_conflict_do_nothing(index_elements=["name_norm"])
        )
        await db.execute(unmatched.values(resolution="ignored", ingredient_id=None))
    else:
        if payload.ingredient is not None:
            action = "created"
            if payload.fdc_id is not None and await db.get(FdcFood, payload.fdc_id) is None:
                raise ApiError(422, "unknown_fdc_id", "No loaded USDA food has that id.")
            ingredient = await _create(db, payload.ingredient)
            if payload.fdc_id is not None:
                await set_preferred_fdc(db, ingredient.id, payload.fdc_id)
        else:
            action = "matched"
            assert payload.ingredient_id is not None  # the schema requires one choice  # noqa: S101
            ingredient = await _chosen(db, payload.ingredient_id)
        await _learn(db, ingredient, name_norm)
        values: dict = {"resolution": "manual", "ingredient_id": ingredient.id}
        if payload.note:
            # The prep tier's stripped words go in front of whatever the line said:
            # "minced; for the sauce", or just "minced" (the 1G conform rule).
            values["note"] = case(
                (
                    or_(RecipeIngredient.note.is_(None), RecipeIngredient.note == ""),
                    literal(payload.note, type_=Text),
                ),
                else_=literal(f"{payload.note}; ", type_=Text) + RecipeIngredient.note,
            )
        await db.execute(unmatched.values(**values))
    await resolve_unmatched(db)
    # Cost snapshots of the recipes whose lines this settled, the decided name's
    # and any the re-run lookup caught (3D).
    await recipe_cost_triggers.after_lines_settled(db, pending)
    remaining, _, _ = await queue_counts(db)
    await db.commit()
    if ingredient is not None:
        await db.refresh(ingredient)
    return ResolveDecisionOut(
        name_norm=name_norm,
        action=action,
        ingredient=IngredientSummary.model_validate(ingredient) if ingredient else None,
        lines=lines,
        recipes=recipes,
        remaining=remaining,
    )
