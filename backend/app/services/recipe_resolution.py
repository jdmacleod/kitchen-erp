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
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Text, case, distinct, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import standard
from app.catalog.names import normalize_name, singulars
from app.catalog.standard_match import match_entry
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.ingest.errors import IngestError
from app.ingest.llm import suggest_recipe_names
from app.models import (
    AppUser,
    Ingredient,
    IngredientAlias,
    Product,
    Recipe,
    RecipeIngredient,
    RecipeNameIgnore,
    RecipePin,
)
from app.models.catalog import FdcFood
from app.recipes.prep import strip_prep
from app.schemas.catalog import IngredientCreate, IngredientSummary
from app.schemas.recipes import (
    RecipePinOut,
    ResolveAskOut,
    ResolveDecisionIn,
    ResolveDecisionOut,
    ResolveName,
    ResolveProposal,
    ResolveQueueOut,
    ResolveRecipe,
)
from app.services import recipe_cost_triggers, usda
from app.services.catalog import (
    IngredientIndex,
    ingredient_index,
    new_ingredient,
    search_ingredients,
    set_preferred_fdc,
)
from app.services.spellings import add_spelling

log = get_logger(__name__)

MAX_PROPOSALS = 3
USDA_PROPOSALS = 2  # per name, from the USDA pool
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


# --- the lookup ------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """Everything one lookup pass reads, loaded once for however many lines.

    ``ask_model`` lets the cascade's model tier call the local model. The queue
    listing never does (a model call per queued name would take minutes);
    :func:`ask` does, for one name a person asked about.
    """

    index: IngredientIndex
    negligible: frozenset[str]
    ignored: frozenset[str]
    ask_model: bool = False


def negligible_names(settings: Settings | None = None) -> frozenset[str]:
    settings = settings or get_settings()
    return frozenset(
        key for key in (normalize_name(name) for name in settings.recipes_negligible_names) if key
    )


async def load_context(
    db: AsyncSession, settings: Settings | None = None, *, ask_model: bool = False
) -> Context:
    ignored = (await db.execute(select(RecipeNameIgnore.name_norm))).scalars().all()
    return Context(
        index=await ingredient_index(db),
        negligible=negligible_names(settings),
        ignored=frozenset(ignored),
        ask_model=ask_model,
    )


def _exact(index: IngredientIndex, key: str) -> Ingredient | None:
    """The one active ingredient whose normalized name or spelling is ``key``.

    Two active ingredients sharing a key (a name that normalizes like another's,
    which the catalog's unique index on the lowercased name does not forbid)
    offer nothing: a wrong resolution costs more than a queued name.
    """
    found: dict[uuid.UUID, Ingredient] = {ing.id: ing for ing in index.by_norm.get(key, [])}
    for ing in index.by_spelling.get(key, []):
        found.setdefault(ing.id, ing)
    if len(found) == 1:
        return next(iter(found.values()))
    return None


def lookup(ctx: Context, name_norm: str) -> Ingredient | None:
    """An exact name or spelling, then an inflection: the only steps without a person."""
    hit = _exact(ctx.index, name_norm)
    if hit is not None:
        return hit
    for single in singulars(name_norm):
        hit = _exact(ctx.index, single)
        if hit is not None:
            return hit
    return None


def apply_lookup(ctx: Context, row: RecipeIngredient) -> bool:
    """Settle one unmatched row from the context; True when it stopped being unmatched."""
    if row.name_norm in ctx.negligible:
        row.negligible = True
        row.resolution = "negligible"
        row.ingredient_id = None
        return True
    if row.name_norm in ctx.ignored:
        row.resolution = "ignored"
        row.ingredient_id = None
        return True
    hit = lookup(ctx, row.name_norm)
    if hit is None:
        return False
    row.ingredient_id = hit.id
    row.resolution = "alias"
    return True


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


Proposer = Callable[[AsyncSession, Context, str], Awaitable[list[ResolveProposal]]]


async def _standard_tier(db: AsyncSession, ctx: Context, name_norm: str) -> list[ResolveProposal]:
    """An exact standard-list entry: its catalog ingredient, or creating one from it."""
    entry = match_entry(name_norm)
    if entry is None:
        return []
    existing = ctx.index.by_slug.get(entry.key)
    if existing is None:
        existing = next(iter(ctx.index.by_name.get(entry.name.lower(), [])), None)
    if existing is not None:
        if not existing.active:
            return []  # an inactive ingredient holds the entry; nothing to offer or create
        return [
            ResolveProposal(
                tier="standard",
                name=existing.name,
                ingredient_id=existing.id,
                standard_key=entry.key,
                category=existing.category,
            )
        ]
    return [
        ResolveProposal(
            tier="standard", name=entry.name, standard_key=entry.key, category=entry.category
        )
    ]


async def _similar_tier(db: AsyncSession, ctx: Context, name_norm: str) -> list[ResolveProposal]:
    """Ranked trigram matches from the 1G search: suggestions only (VC3)."""
    hits = await search_ingredients(db, name_norm, limit=MAX_PROPOSALS)
    return [
        ResolveProposal(
            tier="similar",
            name=hit.name,
            ingredient_id=hit.id,
            category=hit.category,
            matched_spelling=hit.matched_spelling,
        )
        for hit in hits
        if hit.id is not None
    ]


async def _prep_tier(db: AsyncSession, ctx: Context, name_norm: str) -> list[ResolveProposal]:
    """The name without its prep words, looked up exactly; the stripped words ride as the note.

    "minced garlic" offers garlic with the note "minced"; form words are never
    stripped (``app/recipes/data``), so "ground cumin" offers nothing here. The
    remainder is tried as an exact name, spelling or inflection, then as an
    exact standard-list entry. A remainder that is negligible or ignored offers
    nothing: there is no ingredient to point the line at.
    """
    stripped = strip_prep(name_norm)
    if stripped is None:
        return []
    remainder = stripped.remainder
    if remainder in ctx.negligible or remainder in ctx.ignored:
        return []
    hit = lookup(ctx, remainder)
    if hit is not None:
        return [
            ResolveProposal(
                tier="prep",
                name=hit.name,
                ingredient_id=hit.id,
                category=hit.category,
                note=stripped.note,
            )
        ]
    return [
        proposal.model_copy(update={"tier": "prep", "note": stripped.note})
        for proposal in await _standard_tier(db, ctx, remainder)
    ]


async def _usda_tier(db: AsyncSession, ctx: Context, name_norm: str) -> list[ResolveProposal]:
    """Foods from the USDA pool (``fdc_food``) to create an ingredient from.

    Ranked as ``kerp ingredients usda-candidates`` ranks them: Foundation and
    SR Legacy foods resembling the name, the ones USDA's own survey recipes use
    most first. The name without its prep words is what is searched and what the
    new ingredient would be called; the stripped words ride as the note.
    """
    stripped = strip_prep(name_norm)
    text = stripped.remainder if stripped is not None else name_norm
    foods = await usda.candidates(db, text, limit=USDA_PROPOSALS)
    return [
        ResolveProposal(
            tier="usda",
            name=text,
            fdc_id=food.fdc_id,
            fdc_description=food.description,
            note=stripped.note if stripped is not None else None,
        )
        for food in foods
    ]


@dataclass(frozen=True)
class _Candidate:
    """One shortlist entry the model may choose: a catalog ingredient or a standard entry."""

    label: str
    name: str
    ingredient_id: uuid.UUID | None = None
    standard_key: str | None = None
    category: str | None = None


def _trigrams(text: str) -> frozenset[str]:
    """pg_trgm's trigrams of a lowercase phrase, so the standard list ranks like the search."""
    out: set[str] = set()
    for word in text.split():
        padded = f"  {word} "
        out.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return frozenset(out)


def trigram_similarity(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def _shortlist(db: AsyncSession, ctx: Context, name_norm: str) -> list[_Candidate]:
    """Catalog ingredients nearest the name, then standard entries nearest by trigram."""
    size = get_settings().llm_shortlist_size
    out: list[_Candidate] = []
    labels: set[str] = set()
    for hit in await search_ingredients(db, name_norm, limit=size):
        if hit.id is None or hit.name.casefold() in labels:
            continue
        labels.add(hit.name.casefold())
        out.append(_Candidate(hit.name, hit.name, ingredient_id=hit.id, category=hit.category))
    scored: list[tuple[float, standard.StandardEntry]] = []
    for entry in standard.standard_list().ingredients:
        if entry.key in ctx.index.taken_keys or entry.name.lower() in ctx.index.taken_names:
            continue  # the catalog has it: offered above as that ingredient, or not at all
        best = max(
            trigram_similarity(name_norm, normalize_name(form))
            for form in (entry.name, *entry.spellings)
        )
        if best > 0:
            scored.append((best, entry))
    scored.sort(key=lambda t: (-t[0], t[1].name.casefold()))
    for _, entry in scored[:size]:
        if entry.name.casefold() in labels:
            continue
        labels.add(entry.name.casefold())
        out.append(
            _Candidate(entry.name, entry.name, standard_key=entry.key, category=entry.category)
        )
    return out


async def _model_tier(db: AsyncSession, ctx: Context, name_norm: str) -> list[ResolveProposal]:
    """The local model's pick, at most two, from a shortlist it cannot leave.

    Only when the context asks for it and a model is configured (``LLM_MODEL``).
    The reply counts only when it validates against the schema built from the
    shortlist (non-negotiable 7); anything else, and an unreachable or slow
    server, is no suggestion at all. Never applied (VC3).
    """
    if not ctx.ask_model or not get_settings().llm_model:
        return []
    shortlist = await _shortlist(db, ctx, name_norm)
    if not shortlist:
        return []
    try:
        labels = await suggest_recipe_names(name_norm, [c.label for c in shortlist])
    except IngestError as exc:
        log.info("recipe name model gave no suggestion", extra={"code": exc.code})
        return []
    by_label = {c.label: c for c in shortlist}
    return [
        ResolveProposal(
            tier="model",
            name=by_label[label].name,
            ingredient_id=by_label[label].ingredient_id,
            standard_key=by_label[label].standard_key,
            category=by_label[label].category,
        )
        for label in labels
        if label in by_label
    ]


# The cascade in order (07 §3C, VS2): each tier is one proposer, and every
# proposal carries its tier so the page can badge it (UI-7.16). The first two
# steps of the lookup (an exact name or spelling, an inflection) are the only
# ones that resolve a line; everything here only proposes (VC3).
PROPOSERS: list[Proposer] = [_standard_tier, _similar_tier, _prep_tier, _usda_tier, _model_tier]
TIER_ORDER = ("standard", "similar", "prep", "usda", "model")


def _target(proposal: ResolveProposal) -> tuple[str, str]:
    """What choosing the proposal does: use this ingredient, create this entry, or this food."""
    if proposal.ingredient_id is not None:
        return ("ingredient", str(proposal.ingredient_id))
    if proposal.standard_key is not None:
        return ("standard", proposal.standard_key)
    return ("fdc", str(proposal.fdc_id))


async def proposals_for(db: AsyncSession, ctx: Context, name_norm: str) -> list[ResolveProposal]:
    """At most MAX_PROPOSALS per name, higher tiers first, one per target.

    A later tier's proposal for a target an earlier one already offered is
    dropped, except that a prep proposal takes the place of a similar one for
    the same ingredient: the exact hit on the stripped name is the stronger
    claim, and it carries the note. The model is asked last, and only when the
    other tiers left room under the cap.
    """
    out: list[ResolveProposal] = []
    at: dict[tuple[str, str], int] = {}
    for propose in PROPOSERS:
        if propose is _model_tier and len(out) >= MAX_PROPOSALS:
            break
        for proposal in await propose(db, ctx, name_norm):
            key = _target(proposal)
            if key in at:
                held = out[at[key]]
                if proposal.tier == "prep" and held.tier == "similar":
                    out[at[key]] = proposal
                continue
            at[key] = len(out)
            out.append(proposal)
    return out[:MAX_PROPOSALS]


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
            assert payload.ingredient_id is not None  # the schema requires one choice
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


# --- pins ------------------------------------------------------------------------------


async def pins_out(db: AsyncSession, recipe_id: uuid.UUID) -> list[RecipePinOut]:
    rows = await db.execute(
        select(RecipePin.name_norm, Product.id, Product.name, Product.brand)
        .join(Product, Product.id == RecipePin.product_id)
        .where(RecipePin.recipe_id == recipe_id)
        .order_by(RecipePin.name_norm)
    )
    return [
        RecipePinOut(name_norm=name_norm, product_id=product_id, product_name=name, brand=brand)
        for name_norm, product_id, name, brand in rows
    ]


async def _recipe(db: AsyncSession, recipe_id: uuid.UUID) -> Recipe:
    row = await db.get(Recipe, recipe_id)
    if row is None:
        raise ApiError(404, "not_found", "No such recipe.")
    return row


async def _survivor_product(db: AsyncSession, product_id: uuid.UUID) -> Product:
    product = await db.get(Product, product_id)
    if product is None:
        raise ApiError(404, "not_found", "No such product.")
    for _ in range(8):  # merges repoint one level, so this is one hop in practice
        if product.merged_into is None:
            break
        product = await db.get(Product, product.merged_into) or product
    return product


async def _survivor_ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient | None:
    ingredient = await db.get(Ingredient, ingredient_id)
    for _ in range(8):
        if ingredient is None or ingredient.merged_into is None:
            break
        ingredient = await db.get(Ingredient, ingredient.merged_into)
    return ingredient


async def set_pin(
    db: AsyncSession, recipe_id: uuid.UUID, name_norm: str, product_id: uuid.UUID
) -> Recipe:
    """Pin the recipe's lines with this name to a product that fulfils their ingredient.

    Fulfilment is flat (07, "Hierarchy stays flat"): the product's ingredient,
    after following ``merged_into``, must be the line's. The pin stores the
    surviving product when a merged one is named.
    """
    recipe = await _recipe(db, recipe_id)
    lines = (
        (
            await db.execute(
                select(RecipeIngredient).where(
                    RecipeIngredient.recipe_id == recipe.id,
                    RecipeIngredient.name_norm == name_norm,
                )
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        raise ApiError(404, "no_such_line", "No line in this recipe has that name.")
    resolved = {
        line.ingredient_id
        for line in lines
        if line.ingredient_id is not None and line.resolution in ("alias", "manual")
    }
    if not resolved:
        raise ApiError(
            409, "line_unresolved", "Resolve the line to an ingredient before pinning a product."
        )
    product = await _survivor_product(db, product_id)
    ingredient = await _survivor_ingredient(db, product.ingredient_id)
    if ingredient is None or ingredient.id not in resolved:
        line_ingredient = await db.get(Ingredient, next(iter(resolved)))
        raise ApiError(
            409,
            "product_does_not_fulfil",
            "That product is not of the line's ingredient.",
            details={
                "product_ingredient": ingredient.name if ingredient else None,
                "line_ingredient": line_ingredient.name if line_ingredient else None,
            },
        )
    pin = (
        await db.execute(
            select(RecipePin).where(
                RecipePin.recipe_id == recipe.id, RecipePin.name_norm == name_norm
            )
        )
    ).scalar_one_or_none()
    if pin is None:
        db.add(RecipePin(recipe_id=recipe.id, name_norm=name_norm, product_id=product.id))
    else:
        pin.product_id = product.id
    await db.flush()
    await recipe_cost_triggers.after_pin_changed(db, recipe.id)
    await db.commit()
    await db.refresh(recipe)
    return recipe


async def delete_pin(db: AsyncSession, recipe_id: uuid.UUID, name_norm: str) -> None:
    recipe = await _recipe(db, recipe_id)
    pin = (
        await db.execute(
            select(RecipePin).where(
                RecipePin.recipe_id == recipe.id, RecipePin.name_norm == name_norm
            )
        )
    ).scalar_one_or_none()
    if pin is None:
        raise ApiError(404, "not_found", "No pin on that line.")
    await db.delete(pin)
    await db.flush()
    await recipe_cost_triggers.after_pin_changed(db, recipe.id)
    await db.commit()
