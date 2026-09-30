"""The link page: standard names for existing ingredients, and merges (1G).

Registered before the catalog router, so ``/ingredients/link`` is never read
as an ingredient id.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.catalog import standard
from app.catalog.names import normalize_name
from app.schemas.catalog import IngredientOut
from app.schemas.ingredient_link import (
    LinkIn,
    LinkOther,
    LinkPageOut,
    LinkRowOut,
    LinkSuggestion,
    LinkSummaryOut,
    MergeIn,
    MergeOut,
    MergeTarget,
    RenameIn,
    StandardEntryList,
    StandardEntryOut,
)
from app.services import catalog, ingredient_reconcile

router = APIRouter(tags=["ingredient vocabulary"])


def _entry_out(e: standard.StandardEntry) -> StandardEntryOut:
    return StandardEntryOut(
        key=e.key, name=e.name, category=e.category, canonical_unit=e.unit, fdc_id=e.fdc
    )


def _row_out(r: ingredient_reconcile.LinkRow) -> LinkRowOut:
    suggestion = None
    if r.suggestion is not None:
        suggestion = LinkSuggestion(
            **_entry_out(r.suggestion).model_dump(exclude={"category_key"}),
            usda_description=r.usda_description,
        )
    return LinkRowOut(
        id=r.id,
        name=r.name,
        category=r.category,
        canonical_unit=r.canonical_unit,
        products=r.products,
        suggestion=suggestion,
        conflict=LinkOther(**asdict(r.conflict)) if r.conflict else None,
    )


@router.get("/standard-ingredients", response_model=StandardEntryList)
async def standard_ingredients(
    _: CurrentUser, q: str = Query("", max_length=200), limit: int = Query(10, ge=1, le=200)
) -> StandardEntryList:
    """The standard list, filtered by name or spelling; whether taken or not."""
    norm = normalize_name(q)
    entries = standard.standard_list().ingredients
    if norm:
        entries = tuple(
            e for e in entries if any(norm in normalize_name(t) for t in (e.name, *e.spellings))
        )
        entries = tuple(
            sorted(entries, key=lambda e: (not normalize_name(e.name).startswith(norm), e.name))
        )
    return StandardEntryList(items=[_entry_out(e) for e in entries[:limit]])


@router.get("/ingredients/link", response_model=LinkPageOut)
async def link_page(db: DbSession, _: CurrentUser) -> LinkPageOut:
    to_review, skipped = await ingredient_reconcile.link_rows(db)
    return LinkPageOut(
        to_review=[_row_out(r) for r in to_review], skipped=[_row_out(r) for r in skipped]
    )


@router.get("/ingredients/link/summary", response_model=LinkSummaryOut)
async def link_summary(db: DbSession, _: CurrentUser) -> LinkSummaryOut:
    return LinkSummaryOut(**await ingredient_reconcile.summary(db))


@router.post("/ingredients/merge/preview", response_model=MergeOut)
async def merge_preview(body: MergeTarget, db: DbSession, _: CurrentUser) -> MergeOut:
    """What merging would do; nothing is written."""
    done = await ingredient_reconcile.merge_preview(
        db, body.survivor_id, body.loser_id, key=body.standard_key, name=body.name
    )
    return MergeOut(**asdict(done))


@router.post("/ingredients/merge", response_model=MergeOut)
async def merge(body: MergeIn, db: DbSession, _: CurrentUser) -> MergeOut:
    done = await ingredient_reconcile.merge(
        db,
        body.survivor_id,
        body.loser_id,
        key=body.standard_key,
        name=body.name,
        copy_measures=body.copy_measures,
    )
    return MergeOut(**asdict(done))


@router.post("/ingredients/{ingredient_id}/link", response_model=IngredientOut)
async def link(
    ingredient_id: uuid.UUID, body: LinkIn, db: DbSession, _: CurrentUser
) -> IngredientOut:
    """Take a standard name. 409 merge_needed when another ingredient has it."""
    await ingredient_reconcile.link(db, ingredient_id, body.standard_key)
    return await catalog.get_ingredient(db, ingredient_id)


@router.post("/ingredients/{ingredient_id}/rename", response_model=IngredientOut)
async def rename(
    ingredient_id: uuid.UUID, body: RenameIn, db: DbSession, _: CurrentUser
) -> IngredientOut:
    """Take a typed name. 409 merge_needed when another ingredient has it."""
    await ingredient_reconcile.rename(db, ingredient_id, body.name)
    return await catalog.get_ingredient(db, ingredient_id)


@router.post("/ingredients/{ingredient_id}/skip", response_model=IngredientOut)
async def skip(ingredient_id: uuid.UUID, db: DbSession, _: CurrentUser) -> IngredientOut:
    await ingredient_reconcile.set_skipped(db, ingredient_id, True)
    return await catalog.get_ingredient(db, ingredient_id)


@router.post("/ingredients/{ingredient_id}/reopen", response_model=IngredientOut)
async def reopen(ingredient_id: uuid.UUID, db: DbSession, _: CurrentUser) -> IngredientOut:
    await ingredient_reconcile.set_skipped(db, ingredient_id, False)
    return await catalog.get_ingredient(db, ingredient_id)
