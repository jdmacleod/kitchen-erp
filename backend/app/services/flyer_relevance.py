"""Which flyer offers this household plausibly cares about (18, 2U; ruling F10).

A circular prints hundreds of offers; a household cares about a few dozen. An
offer is relevant to a kitchen when, checked in order:

1. its name is a vendor's learned flyer name that isn't marked ignore (``alias``);
2. it and something bought at one of the kitchen's locations in the last
   ``FLYER_RELEVANCE_DAYS``, by product or ingredient name, appear within one
   another (``purchased``);
3. likewise for an ingredient a recipe calls for (``recipe``);
4. for an item on the kitchen's open shopping list (``list``);
5. for a watched ingredient (``watched``).

"Appear within one another" is pg_trgm strict word similarity in either direction,
at least ``FLYER_RELEVANCE_THRESHOLD``. Whole words only, so "salt" is not inside
"salad"; "Fuji Apples 3 lb Bag" holds "apple", and
"Rolled Oats" sits inside "Larkspur Organic Rolled Oats 42 oz". A learned name
marked ignore makes the offer irrelevant whatever else matches. A location with
no kitchen counts every kitchen's purchases and lists.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings


def normalize_name(name: str) -> str:
    """Lower case, accents dropped, every run of other characters one space."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


# The same folding in SQL, for the names offers are compared with: decomposed, then the
# combining accents dropped, so "Açaí" folds to "acai" on both sides. Every query below is
# put together from these constant fragments only; names and ids are bound parameters.
_SQL_NORM = (
    "trim(regexp_replace(regexp_replace(lower(normalize({}, NFKD)), "
    "'[\\u0300-\\u036f]', '', 'g'), '[^a-z0-9]+', ' ', 'g'))"
)
_KITCHEN = "(CAST(:kitchen AS uuid) IS NULL OR vl.home_base_id = CAST(:kitchen AS uuid))"

_TARGETS: dict[str, str] = {
    "purchased": f"""
        SELECT {_SQL_NORM.format("p.name")} FROM purchase_line l
        JOIN purchase pu ON pu.id = l.purchase_id
        JOIN vendor_location vl ON vl.id = pu.vendor_location_id
        JOIN product p ON p.id = l.product_id
        WHERE pu.status = 'committed' AND pu.purchased_at >= CAST(:since AS timestamptz)
          AND l.removed_at IS NULL
          AND {_KITCHEN}
        UNION
        SELECT {_SQL_NORM.format("i.name")} FROM purchase_line l
        JOIN purchase pu ON pu.id = l.purchase_id
        JOIN vendor_location vl ON vl.id = pu.vendor_location_id
        JOIN product p ON p.id = l.product_id
        JOIN ingredient i ON i.id = p.ingredient_id
        WHERE pu.status = 'committed' AND pu.purchased_at >= CAST(:since AS timestamptz)
          AND l.removed_at IS NULL
          AND {_KITCHEN}
    """,  # noqa: S608
    "recipe": f"""
        SELECT DISTINCT {_SQL_NORM.format("i.name")} FROM recipe_ingredient ri
        JOIN ingredient i ON i.id = ri.ingredient_id
        WHERE i.active
    """,  # noqa: S608
    "list": f"""
        SELECT DISTINCT {_SQL_NORM.format("coalesce(i.name, p.name)")}
        FROM shopping_list s
        JOIN shopping_list_item it ON it.list_id = s.id
        LEFT JOIN ingredient i ON i.id = it.ingredient_id
        LEFT JOIN product p ON p.id = it.product_id
        WHERE s.status = 'open' AND it.checked_at IS NULL
          AND (CAST(:kitchen AS uuid) IS NULL OR s.home_base_id = CAST(:kitchen AS uuid))
    """,  # noqa: S608
    "watched": f"""
        SELECT {_SQL_NORM.format("i.name")} FROM ingredient i WHERE i.watch AND i.active
    """,  # noqa: S608
}

_MATCHING = """
    WITH targets AS ({targets}),
         names AS (SELECT n, i FROM unnest(CAST(:names AS text[])) WITH ORDINALITY AS a(n, i))
    SELECT names.i FROM names
    WHERE EXISTS (
        SELECT 1 FROM targets t(name)
        WHERE t.name <> ''
          AND greatest(
              strict_word_similarity(t.name, names.n), strict_word_similarity(names.n, t.name)
          ) >= CAST(:threshold AS numeric)
    )
"""


async def relevance(
    db: AsyncSession,
    *,
    vendor_id: uuid.UUID,
    kitchen_id: uuid.UUID | None,
    names: list[str],
    now: datetime,
) -> list[str | None]:
    """For each normalized name, why it matters to the kitchen, or None."""
    out: list[str | None] = [None] * len(names)
    rows = await db.execute(
        text(
            "SELECT name_norm, ignore FROM flyer_alias "
            "WHERE vendor_id = :vendor AND name_norm = ANY(CAST(:names AS text[]))"
        ),
        {"vendor": vendor_id, "names": names},
    )
    aliases: dict[str, bool] = dict(rows.tuples().all())
    pending: list[int] = []
    for index, name in enumerate(names):
        if name in aliases:
            out[index] = None if aliases[name] else "alias"
        else:
            pending.append(index)
    settings = get_settings()
    params = {
        "kitchen": str(kitchen_id) if kitchen_id else None,
        "since": now - timedelta(days=settings.flyer_relevance_days),
        "threshold": settings.flyer_relevance_threshold,
    }
    for kind, targets in _TARGETS.items():
        if not pending:
            break
        rows = await db.execute(
            text(_MATCHING.format(targets=targets)),
            {**params, "names": [names[i] for i in pending]},
        )
        matched = {pending[i - 1] for (i,) in rows.all()}
        for index in matched:
            out[index] = kind
        pending = [i for i in pending if i not in matched]
    return out
