"""Flyer offers to products (18, 2U-3): resolution, learned names and the offer queue.

```
report ─▶ offer ─▶ exact flyer alias? ─▶ auto (or rejected, when it says ignore)
                 └▶ fuzzy flyer alias, else similar names ─▶ suggested
worker, idle ─▶ one suggested watched offer at a time ─▶ the model's pick, if any
person ─▶ Use this / Not wanted, for every store's offer of that name ─▶ flyer_alias
```

Only a watched ingredient's offer asks a person; every other suggestion waits under
"Other offers" (ruling F13). The model is asked only about watched offers, after the
report, so a report never waits on it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import FlyerAlias, FlyerOffer, FlyerOfferLink, Product
from app.services import flyer_reads, resolution

# The offers that still matter: from the newest run of each source's ad week, still
# valid. A replay's run supersedes the one it re-read (U5).
_CURRENT = """
    WITH runs AS (
        SELECT DISTINCT ON (fr.ad_source_id, fr.ad_week_from) fr.id
        FROM flyer_run fr
        ORDER BY fr.ad_source_id, fr.ad_week_from, fr.created_at DESC
    )
    SELECT o.id FROM flyer_offer o JOIN runs ON runs.id = o.flyer_run_id
    WHERE o.valid_from <= CAST(:today AS date) AND o.valid_to >= CAST(:today AS date)
"""


async def fuzzy_flyer_aliases(
    db: AsyncSession, vendor_id: uuid.UUID, norm: str
) -> list[dict[str, Any]]:
    """A vendor's learned names close to this one: suggestions only, like 2B's."""
    rows = await db.execute(
        text(
            """
            SELECT a.name_norm, a.product_id, a.ignore, p.name AS product_name,
                   similarity(a.name_norm, :t) AS score
            FROM flyer_alias a LEFT JOIN product p ON p.id = a.product_id
            WHERE a.vendor_id = :v AND a.name_norm <> :t
              AND similarity(a.name_norm, :t) >= :thr
            ORDER BY score DESC LIMIT 3
            """
        ),
        {"v": vendor_id, "t": norm, "thr": get_settings().alias_fuzzy_threshold},
    )
    return [
        {
            "kind": "fuzzy",
            "product_id": str(r["product_id"]) if r["product_id"] else None,
            "ignore": r["ignore"],
            "label": r["product_name"] or r["name_norm"],
            "score": str(Decimal(str(r["score"])).quantize(Decimal("0.001"))),
        }
        for r in rows.mappings()
    ]


async def resolve_offers(
    db: AsyncSession,
    vendor_id: uuid.UUID,
    offers: list[FlyerOffer],
    *,
    require_confirmation: bool = False,
) -> None:
    """Give each new offer its link. An exact alias decides it, unless the source asks
    first; anything else is suggested. Names repeat across a circular's pages, so each
    name is looked up once."""
    names = {o.name_norm for o in offers}
    aliases = {
        a.name_norm: a
        for a in (
            await db.execute(
                select(FlyerAlias).where(
                    FlyerAlias.vendor_id == vendor_id, FlyerAlias.name_norm.in_(names)
                )
            )
        ).scalars()
    }
    now = datetime.now(UTC)
    found: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for offer in offers:
        alias = aliases.get(offer.name_norm)
        if alias is not None:
            alias.last_seen_at = now
        if alias is not None and not require_confirmation:
            db.add(
                FlyerOfferLink(
                    flyer_offer_id=offer.id,
                    status="rejected" if alias.ignore else "auto",
                    product_id=alias.product_id,
                    resolution="alias",
                    suggestions=[],
                )
            )
            continue
        if offer.name_norm not in found:
            found[offer.name_norm] = await _suggest(db, vendor_id, offer.name_norm, alias)
        rung, suggestions = found[offer.name_norm]
        db.add(
            FlyerOfferLink(
                flyer_offer_id=offer.id,
                status="suggested",
                resolution=rung,
                suggestions=suggestions,
            )
        )
    # An offer printed without a size reads by its product's (U16).
    await flyer_reads.inherit_sizes(
        db, [o.id for o in offers if (a := aliases.get(o.name_norm)) and a.product_id]
    )


async def _suggest(
    db: AsyncSession, vendor_id: uuid.UUID, norm: str, alias: FlyerAlias | None
) -> tuple[str, list[dict[str, Any]]]:
    if alias is not None:  # a source that asks first still shows what was learned
        product = await db.get(Product, alias.product_id) if alias.product_id else None
        return "alias", [
            {
                "kind": "alias",
                "product_id": str(alias.product_id) if alias.product_id else None,
                "ignore": alias.ignore,
                "label": product.name if product else alias.name_norm,
                "score": "1",
            }
        ]
    fuzzy = await fuzzy_flyer_aliases(db, vendor_id, norm)
    if fuzzy:
        return "fuzzy", fuzzy
    similar = await resolution.shortlist_suggestions(db, norm, vendor_id, {}, use_model=False)
    return ("similar" if similar else "none"), similar


async def rank_once(db: AsyncSession, today: date) -> bool:
    """Ask the model about one current watched offer that only has similar names.

    Every store's offer of that name takes the answer. Returns whether one was asked,
    so an idle worker can come back for the next.
    """
    if resolution._ranker is None:
        return False
    row = (
        await db.execute(
            text(
                f"""
                SELECT o.name_norm, r.vendor_id FROM flyer_offer o
                JOIN flyer_offer_link l ON l.flyer_offer_id = o.id
                JOIN flyer_run fr ON fr.id = o.flyer_run_id
                JOIN ad_source s ON s.id = fr.ad_source_id
                JOIN vendor_location r ON r.id = s.vendor_location_id
                WHERE o.id IN ({_CURRENT}) AND o.relevance = 'watched'
                  AND l.status = 'suggested' AND l.resolution IN ('similar', 'none')
                ORDER BY o.created_at LIMIT 1
                """  # noqa: S608  constant fragments only
            ),
            {"today": today},
        )
    ).first()
    if row is None:
        return False
    norm, vendor_id = row
    suggestions = await resolution.shortlist_suggestions(db, norm, vendor_id, {})
    links = await _group_links(db, vendor_id, norm, today, ("similar", "none"))
    for link in links:
        link.resolution = "ranked"
        if suggestions and suggestions[0]["kind"] == "llm":
            link.suggestions = suggestions
    await db.commit()
    return True


async def _group_links(
    db: AsyncSession,
    vendor_id: uuid.UUID,
    norm: str,
    today: date,
    rungs: tuple[str, ...] | None = None,
) -> list[FlyerOfferLink]:
    """Every current suggested link of one vendor's printed name, across its stores."""
    rung = "AND l.resolution = ANY(CAST(:rungs AS text[]))" if rungs else ""
    ids = (
        await db.execute(
            text(
                f"""
                SELECT l.flyer_offer_id FROM flyer_offer_link l
                JOIN flyer_offer o ON o.id = l.flyer_offer_id
                JOIN flyer_run fr ON fr.id = o.flyer_run_id
                JOIN ad_source s ON s.id = fr.ad_source_id
                JOIN vendor_location r ON r.id = s.vendor_location_id
                WHERE o.id IN ({_CURRENT}) AND l.status = 'suggested'
                  AND r.vendor_id = :vendor AND o.name_norm = :norm {rung}
                """  # noqa: S608  constant fragments only
            ),
            {"today": today, "vendor": vendor_id, "norm": norm, "rungs": list(rungs or ())},
        )
    ).scalars()
    locked = (
        await db.execute(
            select(FlyerOfferLink)
            .where(FlyerOfferLink.flyer_offer_id.in_(list(ids)))
            .with_for_update()
        )
    ).scalars()
    # Checked again under the lock: a person answering the same group a moment
    # earlier has already decided these, and their answer stands.
    return [link for link in locked if link.status == "suggested"]


async def link_unlinked_once(db: AsyncSession, today: date) -> bool:
    """Give current offers kept before 2U-3 their links, one source's run at a time,
    so a circular already read shows in the queue without a replay. Returns whether
    there was one."""
    row = (
        await db.execute(
            text(
                f"""
                SELECT o.flyer_run_id, r.vendor_id, s.require_confirmation
                FROM flyer_offer o
                JOIN flyer_run fr ON fr.id = o.flyer_run_id
                JOIN ad_source s ON s.id = fr.ad_source_id
                JOIN vendor_location r ON r.id = s.vendor_location_id
                WHERE o.id IN ({_CURRENT})
                  AND NOT EXISTS (
                      SELECT 1 FROM flyer_offer_link l WHERE l.flyer_offer_id = o.id
                  )
                LIMIT 1
                """  # noqa: S608  constant fragments only
            ),
            {"today": today},
        )
    ).first()
    if row is None:
        return False
    run_id, vendor_id, require_confirmation = row
    offers = list(
        (
            await db.execute(
                select(FlyerOffer).where(
                    FlyerOffer.flyer_run_id == run_id,
                    ~select(FlyerOfferLink.flyer_offer_id)
                    .where(FlyerOfferLink.flyer_offer_id == FlyerOffer.id)
                    .exists(),
                )
            )
        ).scalars()
    )
    await resolve_offers(db, vendor_id, offers, require_confirmation=require_confirmation)
    await db.commit()
    return True


# --- the offer queue ------------------------------------------------------------------

_QUEUE = f"""
    SELECT o.id, o.name_as_printed, o.name_norm, o.relevance, o.price_basis, o.price,
           o.basis_qty, o.discount, o.size_qty, o.size_unit, o.requires_loyalty,
           o.limit_qty, o.valid_from, o.valid_to, o.created_at, l.suggestions,
           v.id AS vendor_id, v.name AS vendor_name, r.id AS location_id,
           r.name AS location_name, s.require_confirmation
    FROM flyer_offer o
    JOIN flyer_offer_link l ON l.flyer_offer_id = o.id
    JOIN flyer_run fr ON fr.id = o.flyer_run_id
    JOIN ad_source s ON s.id = fr.ad_source_id
    JOIN vendor_location r ON r.id = s.vendor_location_id
    JOIN vendor v ON v.id = r.vendor_id
    WHERE o.id IN ({_CURRENT}) AND l.status = 'suggested'
    ORDER BY v.name, o.name_norm, r.name
"""  # noqa: S608  constant fragments only


async def offer_queue(db: AsyncSession, today: date) -> dict[str, list[dict[str, Any]]]:
    """The suggested offers, one group per vendor and printed name, split into the
    ones that ask a person and the other offers that wait quietly (F13). A watched
    offer asks, and so does every offer from a source that asks first: its reading
    isn't trusted yet (2W)."""
    groups: dict[tuple[uuid.UUID, str], dict[str, Any]] = {}
    for r in (await db.execute(text(_QUEUE), {"today": today})).mappings():
        key = (r["vendor_id"], r["name_norm"])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "vendor": {"id": r["vendor_id"], "name": r["vendor_name"]},
                "name_norm": r["name_norm"],
                "name_as_printed": r["name_as_printed"],
                "watched": False,
                "suggestions": r["suggestions"] or [],
                "offers": [],
                "first_seen_at": r["created_at"],
            }
        group["watched"] = (
            group["watched"] or r["relevance"] == "watched" or r["require_confirmation"]
        )
        group["first_seen_at"] = min(group["first_seen_at"], r["created_at"])
        group["offers"].append(
            {
                "id": r["id"],
                "location": {"id": r["location_id"], "name": r["location_name"]},
                **{
                    k: r[k]
                    for k in (
                        "name_as_printed",
                        "price_basis",
                        "price",
                        "basis_qty",
                        "discount",
                        "size_qty",
                        "size_unit",
                        "requires_loyalty",
                        "limit_qty",
                        "valid_from",
                        "valid_to",
                    )
                },
            }
        )
    items = list(groups.values())
    return {
        "watched": [g for g in items if g["watched"]],
        "other": [g for g in items if not g["watched"]],
    }


async def decide(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    vendor_id: uuid.UUID,
    name_norm: str,
    product_id: uuid.UUID | None,
    ignore: bool,
    today: date,
) -> int:
    """A person's answer for one vendor's printed name: every current suggested offer
    of it takes the answer, and the name is learned for next week (U7)."""
    if product_id is not None and await db.get(Product, product_id) is None:
        raise ApiError(422, "product_not_found", "No such product.")
    links = await _group_links(db, vendor_id, name_norm, today)
    if not links:
        raise ApiError(404, "not_found", "No offers of that name are waiting.")
    now = datetime.now(UTC)
    for link in links:
        link.status = "rejected" if ignore else "confirmed"
        link.product_id = None if ignore else product_id
        link.resolution = "person"
        link.decided_by = user_id
        link.decided_at = now
    if not ignore:
        await flyer_reads.inherit_sizes(db, [link.flyer_offer_id for link in links])
    await upsert_flyer_alias(db, vendor_id, name_norm, None if ignore else product_id)
    await db.commit()
    return len(links)


async def upsert_flyer_alias(
    db: AsyncSession, vendor_id: uuid.UUID, name_norm: str, product_id: uuid.UUID | None
) -> FlyerAlias:
    """Learn or count up a vendor's printed name, as receipt confirmations do (2D).
    Re-pointing it resets the count."""
    alias = (
        await db.execute(
            select(FlyerAlias).where(
                FlyerAlias.vendor_id == vendor_id, FlyerAlias.name_norm == name_norm
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    ignore = product_id is None
    if alias is None:
        alias = FlyerAlias(
            vendor_id=vendor_id,
            name_norm=name_norm,
            product_id=product_id,
            ignore=ignore,
            confirmed_count=1,
            last_seen_at=now,
        )
        db.add(alias)
    elif (alias.product_id, alias.ignore) == (product_id, ignore):
        alias.confirmed_count += 1
        alias.last_seen_at = now
    else:
        alias.product_id = product_id
        alias.ignore = ignore
        alias.confirmed_count = 1
        alias.last_seen_at = now
    await db.flush()
    return alias
