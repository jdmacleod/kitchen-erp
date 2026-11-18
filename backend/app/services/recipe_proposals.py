"""The resolve cascade's proposals for one recipe-line name (07, 3C, VS2).

Each tier is one proposer in ``PROPOSERS``: an exact standard-list entry,
trigram matches, the name without its prep words, foods from the USDA pool,
and, only when a person asks about one name, the local model's pick from a
shortlist it cannot leave. ``services/recipe_resolution`` describes the lookup
and the queue these proposals fill.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories, standard
from app.catalog.names import normalize_name
from app.catalog.standard_match import exact_entry, match_entry
from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingest.errors import IngestError
from app.ingest.llm import suggest_recipe_names
from app.recipes.prep import shares_food_word, strip_prep
from app.schemas.recipes import ResolveProposal
from app.services import usda
from app.services.catalog import search_ingredients
from app.services.recipe_lookup import Context, lookup

log = get_logger(__name__)

MAX_PROPOSALS = 3
USDA_PROPOSALS = 2  # per name, from the USDA pool
Proposer = Callable[[AsyncSession, Context, str], Awaitable[list[ResolveProposal]]]


async def _standard_tier(db: AsyncSession, ctx: Context, name_norm: str) -> list[ResolveProposal]:
    """A standard-list entry, prep words left to the prep tier: its ingredient, or one to create."""
    entry = match_entry(name_norm, prep=False)
    if entry is None:
        return []
    existing = ctx.index.by_slug.get(entry.key)
    if existing is None:
        existing = next(iter(ctx.index.by_name.get(entry.name.lower(), [])), None)
    if existing is not None and not existing.active:
        return []  # an inactive ingredient holds the entry; nothing to offer or create
    # Its ingredient when the catalog has one, or the entry to create one from.
    return [
        ResolveProposal(
            tier="standard",
            name=existing.name if existing else entry.name,
            ingredient_id=existing.id if existing else None,
            standard_key=entry.key,
            category=existing.category if existing else entry.category,
            exact=exact_entry(name_norm) is entry,
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
        if hit.id is not None and shares_food_word(name_norm, hit.name, hit.matched_spelling)
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

    Ranked as ``kerp ingredients usda-candidates`` ranks them, used ones first. The
    name without its prep words is what is searched and what the new ingredient
    would be called; the stripped words ride as the note, the food group as its
    category (#344)."""
    stripped = strip_prep(name_norm)
    text = stripped.remainder if stripped is not None else name_norm
    foods = await usda.candidates(db, text, limit=USDA_PROPOSALS, fit_name=text)
    return [
        ResolveProposal(
            tier="usda",
            name=text,
            fdc_id=food.fdc_id,
            fdc_description=food.description,
            category=categories.key(food.category),
            note=stripped.note if stripped is not None else None,
        )
        for food in foods
        if shares_food_word(text, food.description)
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
        if (propose is _model_tier and len(out) >= MAX_PROPOSALS) or (out and out[0].exact):
            break  # an exact standard entry settles it: lower tiers only add noise and time
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
