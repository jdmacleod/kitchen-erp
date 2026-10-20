"""The To identify queue: groups of unidentified receipt lines, naming them in bulk,
and the kitchen supply categories a line can be marked with (04, 2D, 2I, 2T).

Split from purchases.py, which had outgrown the file size limit; the paths and the
OpenAPI tag are unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.schemas.purchases import (
    NameProductsIn,
    NameProductsOut,
    NamingAsked,
    NamingList,
    QueueApplied,
    QueueApply,
    QueueList,
    SupplyCategoryList,
)
from app.services import identify_queue, naming, naming_model, proposals, supplies

router = APIRouter(tags=["purchases"])


@router.get("/supply-categories", response_model=SupplyCategoryList)
async def supply_categories(_: CurrentUser) -> SupplyCategoryList:
    """The kitchen supply categories a line can be marked with, in order (04, 2T)."""
    return SupplyCategoryList(items=supplies.categories())  # type: ignore[arg-type]


@router.get("/to-identify", response_model=QueueList)
async def to_identify(_: CurrentUser, db: DbSession) -> QueueList:
    """Every waiting group, with what bulk naming would suggest for it (2D, 2I)."""
    groups = await identify_queue.to_identify(db)
    codes = await identify_queue.queue_codes(db, groups)
    others = identify_queue.elsewhere(groups)
    rows = await naming.naming_rows(db, groups)
    items = []
    for g, row in zip(groups, rows, strict=True):
        key = (str(g["vendor_id"]), g["raw_text_norm"])
        items.append(
            {
                "vendor": {
                    "id": g["vendor_id"],
                    "name": g["vendor_name"],
                    "search_url": g["search_url"],
                },
                "raw_text_norm": g["raw_text_norm"],
                "line_count": g["line_count"],
                "lines": g["lines"],
                "code": codes.get(key),
                "suggested_name": row["name"] or None,
                "suggested_brand": row["brand"],
                "candidates": row["candidates"],
                "elsewhere": others.get(key, []),
            }
        )
    return QueueList(items=items)


@router.post("/to-identify/apply", response_model=QueueApplied)
async def apply_to_identify(payload: QueueApply, user: CurrentUser, db: DbSession) -> QueueApplied:
    n = await identify_queue.apply_to_identify(
        db,
        user,
        vendor_id=payload.vendor_id,
        raw_text_norm=payload.raw_text_norm,
        product_id=payload.product_id,
        ignore=payload.ignore,
        line_ids=payload.line_ids,
        supply_category=payload.supply_category,
    )
    return QueueApplied(applied=n)


@router.get("/to-identify/naming", response_model=NamingList)
async def naming_rows(_: CurrentUser, db: DbSession) -> NamingList:
    """Every waiting group with a suggested product, for naming them in bulk (2I).
    With NAMING_AUTO_SUGGEST on, the rows never asked about go to the model now (#387)."""
    rows = await naming.naming_rows(db)
    if get_settings().naming_auto_suggest:
        await naming_model.queue_unasked(db, rows)
    return NamingList(items=rows)


@router.post("/to-identify/naming/suggest", response_model=NamingAsked)
async def suggest_names(_: CurrentUser, db: DbSession) -> NamingAsked:
    """Ask the model about the rows the wording couldn't name; the worker answers (N3)."""
    return NamingAsked(queued=await naming.request_suggestions(db))


@router.post("/to-identify/name-products", response_model=NameProductsOut)
async def name_products(
    payload: NameProductsIn, user: CurrentUser, db: DbSession
) -> NameProductsOut:
    """Create each confirmed row's product and identify its lines; each row stands alone."""
    results = await naming.name_products(db, user, payload.rows)
    # Proposals clipped before these products existed now see them (2P).
    for product_id in dict.fromkeys(r["product_id"] for r in results if r.get("product_id")):
        await proposals.rematch_pending(db, product_id)
    return NameProductsOut(results=results)
