"""The resolution ladder (Phase 2D): barcode, confirmed alias, fuzzy alias
suggestion, model suggestion, similar names, human. Only the first two resolve
without a person.

Also: alias learning on every human confirmation, the price sanity check, commit
and reopen of receipt purchases, and the to-identify queue.
"""

from __future__ import annotations

import statistics
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog import receipt_codes
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.models import (
    AppUser,
    IngestJob,
    Ingredient,
    PriceObservation,
    PriceObservationVoid,
    Product,
    ProductIdentifier,
    Purchase,
    PurchaseLine,
    ReceiptAlias,
)
from app.models.geo import Vendor, VendorLocation
from app.services import pricebook
from app.services.catalog import search_products
from app.services.normalize import NORMALIZE_VERSION, normalize_receipt_text
from app.services.purchases import ensure_not_voided, get_purchase, live_observations
from app.services.units import build_context
from app.units import CanonicalQty, convert

log = get_logger(__name__)

RESOLVE_VERSION = "2"

# Shortlist hits offered as similar names when the model gives nothing (04, 2D).
SIMILAR_LIMIT = 3

# A model ranker: given the normalized line text and a shortlist of candidate
# dicts ({"id", "name", "brand", "ingredient"}), return {"product_id": str|None,
# "confidence": Decimal|float|None}. Injected so the default suite is offline.
Ranker = Callable[[str, list[dict[str, Any]]], Awaitable[dict[str, Any]]]
_ranker: Ranker | None = None


def set_ranker(ranker: Ranker | None) -> None:
    global _ranker  # noqa: PLW0603
    _ranker = ranker


def install_default_ranker() -> bool:
    """Wire the ingest pipeline's model client as the ranker, when configured.

    Returns True when a ranker was installed. Safe to call when the ingest
    package is absent or the setting is off; the ladder then stops at rung 3.
    """
    if not get_settings().llm_ranker_enabled:
        set_ranker(None)
        return False
    try:
        from app.ingest.llm import rank_products
    except ImportError:
        log.info("no model ranker available; resolution offers similar names instead")
        set_ranker(None)
        return False
    set_ranker(rank_products)
    return True


async def _identified(
    db: AsyncSession, codes: receipt_codes.LineCodes, vendor_id: uuid.UUID | None
) -> Product | None:
    """The active product a line's codes name, if one does."""
    for code in codes.gtins:
        product = await _by_identifier(db, "gtin", code, None)
        if product is not None:
            return product
    if vendor_id is None:
        return None
    for scheme, value in codes.positioned:
        product = await _by_identifier(db, scheme, value, vendor_id)
        if product is not None:
            return product
    return None


async def _by_identifier(
    db: AsyncSession, scheme: str, value: str, vendor_id: uuid.UUID | None
) -> Product | None:
    vendor_clause = (
        ProductIdentifier.vendor_id == vendor_id
        if vendor_id is not None
        else ProductIdentifier.vendor_id.is_(None)
    )
    return (
        await db.execute(
            select(Product)
            .join(ProductIdentifier, ProductIdentifier.product_id == Product.id)
            .where(
                ProductIdentifier.scheme == scheme,
                ProductIdentifier.value == value,
                vendor_clause,
                Product.active,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _loose_code_suggestions(
    db: AsyncSession, codes: receipt_codes.LineCodes, vendor_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    """Digit runs outside the vendor's code position that match one of its codes."""
    if vendor_id is None or not codes.loose:
        return []
    rows = (
        await db.execute(
            select(ProductIdentifier.value, Product.id, Product.name)
            .join(Product, Product.id == ProductIdentifier.product_id)
            .where(
                ProductIdentifier.vendor_id == vendor_id,
                ProductIdentifier.scheme.in_(receipt_codes.VENDOR_SCHEMES),
                ProductIdentifier.value.in_(codes.loose),
                Product.active,
            )
        )
    ).all()
    return [
        {
            "kind": "code",
            "product_id": str(pid),
            "ignore": False,
            "label": f"{name} (code {value})",
            "score": "1",
        }
        for value, pid, name in rows
    ]


async def code_offers(db: AsyncSession, purchase: Purchase) -> dict[uuid.UUID, dict[str, str]]:
    """Per line, the code its chosen product could be remembered by (04, 2K)."""
    location = purchase.vendor_location
    vendor = location.vendor if location is not None else None
    if vendor is None:
        return {}
    out: dict[uuid.UUID, dict[str, str]] = {}
    for line in purchase.lines:
        if line.line_kind != "item" or line.product_id is None:
            continue
        offer = receipt_codes.read(
            line.raw_text or "", vendor.code_position, vendor.rw_layout
        ).offer
        if offer is None or line.resolution in ("identifier", "barcode"):
            continue
        known = await _by_identifier(db, offer[0], offer[1], vendor.id)
        if known is None:
            out[line.id] = {"scheme": offer[0], "value": offer[1]}
    return out


async def queue_codes(
    db: AsyncSession, groups: list[dict[str, Any]]
) -> dict[tuple[str, str | None], dict[str, str]]:
    """Per to-identify group, the code its lines carry, read with its vendor's layout."""
    out: dict[tuple[str, str | None], dict[str, str]] = {}
    vendors: dict[str, Vendor | None] = {}
    for group in groups:
        key = str(group["vendor_id"])
        if key not in vendors:
            vendors[key] = await db.get(Vendor, uuid.UUID(key))
        vendor = vendors[key]
        if vendor is None or not group["lines"]:
            continue
        raw = group["lines"][0].get("raw_text") or ""
        offer = receipt_codes.read(raw, vendor.code_position, vendor.rw_layout).offer
        if offer is not None:
            out[(key, group["raw_text_norm"])] = {"scheme": offer[0], "value": offer[1]}
    return out


async def remember_code(
    db: AsyncSession, purchase_id: uuid.UUID, line_id: uuid.UUID
) -> ProductIdentifier:
    """Record the line's code for its product at this vendor; only on a person's click."""
    purchase = await get_purchase(db, purchase_id, lock=True)
    line = await _line_of(purchase, line_id)
    offer = (await code_offers(db, purchase)).get(line.id)
    if offer is None or line.product_id is None:
        raise ApiError(409, "no_code", "This line has no code to remember for its product.")
    vendor_id = purchase.vendor_location.vendor_id  # type: ignore[union-attr]
    owner = (
        await db.execute(
            select(ProductIdentifier.product_id).where(
                ProductIdentifier.scheme == offer["scheme"],
                ProductIdentifier.value == offer["value"],
                ProductIdentifier.vendor_id == vendor_id,
            )
        )
    ).scalar_one_or_none()
    if owner is not None and owner != line.product_id:
        other = await db.get(Product, owner)
        raise ApiError(
            409,
            "identifier_taken",
            f"{other.name if other else 'Another product'} already has that code here.",
            {"product_id": str(owner), "name": other.name if other else None},
        )
    identifier = ProductIdentifier(
        product_id=line.product_id,
        scheme=offer["scheme"],
        value=offer["value"],
        vendor_id=vendor_id,
        source="receipt",
    )
    db.add(identifier)
    await db.commit()
    return identifier


# --- price sanity -----------------------------------------------------------


async def implied_unit_price(
    db: AsyncSession, product: Product, qty: Decimal | None, unit: str | None, price: Decimal
) -> Decimal | None:
    if qty is None or unit is None or price is None:
        return None
    ingredient = (
        await db.execute(
            select(Ingredient)
            .options(selectinload(Ingredient.measures))
            .where(Ingredient.id == product.ingredient_id)
        )
    ).scalar_one()
    result = convert(qty, unit, await build_context(db, ingredient, product))
    if not isinstance(result, CanonicalQty) or result.qty == 0:
        return None
    return pricebook.unit_price(price, result.qty)


async def recent_median_unit_price(db: AsyncSession, product_id: uuid.UUID) -> Decimal | None:
    rows = await db.execute(
        text(
            """
            SELECT norm_unit_price FROM price_current
            WHERE product_id = :pid AND norm_status = 'ok'
            ORDER BY observed_at DESC LIMIT 5
            """
        ),
        {"pid": product_id},
    )
    prices = [Decimal(str(r[0])) for r in rows if r[0] is not None]
    return statistics.median(prices) if prices else None


async def is_price_outlier(
    db: AsyncSession, product: Product, qty: Decimal | None, unit: str | None, price: Decimal
) -> bool:
    implied = await implied_unit_price(db, product, qty, unit, price)
    median = await recent_median_unit_price(db, product.id)
    if implied is None or median is None or median == 0 or implied == 0:
        return False
    factor = get_settings().price_outlier_factor
    ratio = implied / median
    return ratio > factor or ratio < (Decimal(1) / factor)


# --- aliases ----------------------------------------------------------------


async def upsert_alias(
    db: AsyncSession, vendor_id: uuid.UUID, raw_text_norm: str, product_id: uuid.UUID | None
) -> ReceiptAlias:
    """Every human confirmation writes or updates an alias. Re-pointing resets the count."""
    if not raw_text_norm:
        raise ApiError(422, "empty_alias", "The line has no text to learn from.")
    alias = (
        await db.execute(
            select(ReceiptAlias).where(
                ReceiptAlias.vendor_id == vendor_id, ReceiptAlias.raw_text_norm == raw_text_norm
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    disposition = "product" if product_id is not None else "ignore"
    if alias is None:
        alias = ReceiptAlias(
            vendor_id=vendor_id,
            raw_text_norm=raw_text_norm,
            disposition=disposition,
            product_id=product_id,
            confirmed_count=1,
            last_seen_at=now,
        )
        db.add(alias)
    elif alias.disposition == disposition and alias.product_id == product_id:
        alias.confirmed_count += 1
        alias.last_seen_at = now
    else:
        alias.disposition = disposition
        alias.product_id = product_id
        alias.confirmed_count = 1
        alias.last_seen_at = now
    await db.flush()
    return alias


async def fuzzy_aliases(db: AsyncSession, vendor_id: uuid.UUID, norm: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT id, raw_text_norm, disposition, product_id,
                   similarity(raw_text_norm, :t) AS score
            FROM receipt_alias
            WHERE vendor_id = :v AND raw_text_norm <> :t
              AND similarity(raw_text_norm, :t) >= :thr
            ORDER BY score DESC LIMIT 3
            """
        ),
        {"v": vendor_id, "t": norm, "thr": get_settings().alias_fuzzy_threshold},
    )
    return [
        {
            "kind": "fuzzy",
            "product_id": str(r["product_id"]) if r["product_id"] else None,
            "ignore": r["disposition"] == "ignore",
            "label": r["raw_text_norm"],
            "score": str(Decimal(str(r["score"])).quantize(Decimal("0.001"))),
        }
        for r in rows.mappings()
    ]


# --- the ladder -------------------------------------------------------------


async def _shortlist(db: AsyncSession, line: PurchaseLine, norm: str) -> list[dict[str, Any]]:
    hits = await search_products(db, norm, get_settings().llm_shortlist_size)
    out: list[dict[str, Any]] = []
    factor = get_settings().price_plausibility_factor
    for hit in hits:
        product = await db.get(Product, hit.id)
        if product is None:
            continue
        # Narrow by price plausibility where history exists.
        median = await recent_median_unit_price(db, product.id)
        if median and line.line_total is not None:
            implied = await implied_unit_price(db, product, line.qty, line.unit, line.line_total)
            if implied is not None and implied > 0:
                ratio = implied / median
                if ratio > factor or ratio < Decimal(1) / factor:
                    continue
        out.append(
            {
                "id": str(hit.id),
                "name": hit.name,
                "brand": hit.brand,
                "ingredient": hit.ingredient.name,
                "match": hit.match,
                "score": str(hit.score),
            }
        )
    return out


async def resolve_line(db: AsyncSession, purchase: Purchase, line: PurchaseLine) -> dict[str, Any]:
    """Run the ladder for one item line. Mutates the line; does not commit."""
    vendor_id = purchase.vendor_location.vendor_id if purchase.vendor_location else None
    norm = normalize_receipt_text(line.raw_text or "")
    line.raw_text_norm = norm
    suggestions: list[dict[str, Any]] = []
    record: dict[str, Any] = {"line_id": str(line.id), "norm": norm, "rung": None}
    if line.line_kind != "item":
        line.suggestions = []
        record["rung"] = "not_item"
        return record

    # 1. Identifier (2K): a GTIN, a weighed-item label, or a code where the vendor
    # prints them. Read from the raw text, before normalization drops leading codes.
    vendor = await db.get(Vendor, vendor_id) if vendor_id is not None else None
    codes = receipt_codes.read(
        line.raw_text or "",
        vendor.code_position if vendor else None,
        vendor.rw_layout if vendor else None,
    )
    record["code"] = codes.offer[1] if codes.offer else None
    product = await _identified(db, codes, vendor_id)
    if product is not None:
        line.product_id = product.id
        line.resolution = "identifier"
        line.resolved_by = None
        line.resolution_confidence = Decimal("1")
        # The same price check as an alias (04, 2K).
        flags = [f for f in line.flags if f != "price_outlier"]
        if await is_price_outlier(db, product, line.qty, line.unit, line.line_total):
            flags.append("price_outlier")
        line.flags = flags
        line.suggestions = []
        record["rung"] = "identifier"
        record["price_outlier"] = "price_outlier" in flags
        return record
    # A digit run anywhere else is only ever a suggestion.
    suggestions.extend(await _loose_code_suggestions(db, codes, vendor_id))

    if vendor_id is not None and norm:
        # 2. Exact alias.
        alias = (
            await db.execute(
                select(ReceiptAlias).where(
                    ReceiptAlias.vendor_id == vendor_id, ReceiptAlias.raw_text_norm == norm
                )
            )
        ).scalar_one_or_none()
        if alias is not None:
            if alias.confirmed_count >= 1:
                alias.last_seen_at = datetime.now(UTC)
                if alias.disposition == "ignore":
                    line.product_id = None
                    line.resolution = "ignored"
                    line.resolved_by = None
                    line.suggestions = []
                    record["rung"] = "alias"
                    return record
                product = await db.get(Product, alias.product_id)
                if product is not None:
                    line.product_id = product.id
                    line.resolution = "alias"
                    line.resolved_by = None
                    line.resolution_confidence = Decimal("1")
                    flags = [f for f in line.flags if f != "price_outlier"]
                    if await is_price_outlier(db, product, line.qty, line.unit, line.line_total):
                        flags.append("price_outlier")
                    line.flags = flags
                    line.suggestions = []
                    record["rung"] = "alias"
                    record["price_outlier"] = "price_outlier" in flags
                    return record
            else:
                suggestions.append(
                    {
                        "kind": "alias_unconfirmed",
                        "product_id": str(alias.product_id) if alias.product_id else None,
                        "ignore": alias.disposition == "ignore",
                        "label": alias.raw_text_norm,
                        "score": "1",
                    }
                )
        # 3. Fuzzy alias: suggestion only.
        suggestions.extend(await fuzzy_aliases(db, vendor_id, norm))

    # 4. Model suggestion: rank a shortlist; never resolves on its own.
    # 5. With no model, or no usable answer, the shortlist's best hits are offered
    #    as similar names, never resolved on their own.
    if not suggestions and norm:
        shortlist = await _shortlist(db, line, norm)
        record["shortlist"] = [c["id"] for c in shortlist]
        if shortlist and _ranker is not None:
            try:
                answer = await _ranker(norm, shortlist)
            except Exception as exc:  # the model is optional; never block review
                log.warning("ranker failed", extra={"error": type(exc).__name__})
                answer = {"product_id": None, "confidence": None, "error": type(exc).__name__}
            pid = answer.get("product_id")
            allowed = {c["id"] for c in shortlist}
            if pid is not None and str(pid) in allowed:
                suggestions.append(
                    {
                        "kind": "llm",
                        "product_id": str(pid),
                        "ignore": False,
                        "label": next(c["name"] for c in shortlist if c["id"] == str(pid)),
                        "score": str(answer.get("confidence") or ""),
                    }
                )
            elif pid is not None:
                record["llm_rejected"] = "outside_shortlist"
        if not suggestions:
            suggestions.extend(
                {
                    "kind": "similar",
                    "product_id": c["id"],
                    "ignore": False,
                    "label": c["name"],
                    "score": c["score"],
                }
                for c in shortlist[:SIMILAR_LIMIT]
            )
            record["similar"] = len(suggestions)

    line.suggestions = suggestions
    if line.resolution in ("barcode", "identifier", "alias"):
        # Re-resolving a previously auto-resolved line that no longer matches.
        line.resolution = "unmatched"
        line.product_id = None
    record["rung"] = "human" if suggestions else "none"
    return record


async def resolve_purchase(db: AsyncSession, purchase_id: uuid.UUID) -> dict[str, Any]:
    """Run the ladder over every unresolved item line. Commits. Returns stage output."""
    purchase = await get_purchase(db, purchase_id, lock=True)
    records = []
    for line in purchase.lines:
        if (
            line.resolution in ("manual", "ignored", "barcode", "identifier", "alias")
            and line.resolved_by
        ):
            continue  # a person already decided
        if line.resolution == "manual":
            continue
        records.append(await resolve_line(db, purchase, line))
    await db.commit()
    return {
        "normalize_version": NORMALIZE_VERSION,
        "resolve_version": RESOLVE_VERSION,
        "lines": records,
        "auto_resolved": sum(1 for r in records if r["rung"] in ("identifier", "alias")),
    }


# --- human decisions ----------------------------------------------------------


async def _line_of(purchase: Purchase, line_id: uuid.UUID) -> PurchaseLine:
    for line in purchase.lines:
        if line.id == line_id:
            return line
    raise ApiError(404, "not_found", "No such line on this purchase.")


async def decide_line(
    db: AsyncSession,
    user: AppUser,
    purchase_id: uuid.UUID,
    line_id: uuid.UUID,
    *,
    product_id: uuid.UUID | None,
    ignore: bool,
    accepted_kind: str | None = None,
) -> Purchase:
    """Choose a product, accept a suggestion, or mark the line ignored. Writes an alias.

    On a committed purchase the observation is emitted now, dated to the purchase.
    """
    purchase = await get_purchase(db, purchase_id, lock=True)
    ensure_not_voided(purchase)
    line = await _line_of(purchase, line_id)
    if line.line_kind != "item":
        raise ApiError(422, "not_an_item", "Only item lines resolve to products.")
    if ignore == (product_id is not None):
        raise ApiError(422, "validation_error", "Give a product_id or ignore, not both.")
    if product_id is not None and await db.get(Product, product_id) is None:
        raise ApiError(404, "not_found", "No such product.")
    live = await live_observations(db, purchase)
    if line.id in live:
        await pricebook.void(db, live[line.id], "line re-pointed in review", user)
    line.product_id = product_id
    line.resolution = "ignored" if ignore else (accepted_kind or "manual")
    if line.resolution not in ("alias", "fuzzy", "llm", "similar", "manual", "ignored", "barcode"):
        line.resolution = "manual"
    line.resolved_by = user.id
    line.resolution_confidence = None
    line.suggestions = []
    line.flags = [f for f in line.flags if f != "price_outlier"]
    if purchase.vendor_location is not None:
        norm = line.raw_text_norm or normalize_receipt_text(line.raw_text or "")
        line.raw_text_norm = norm
        if norm:
            await upsert_alias(db, purchase.vendor_location.vendor_id, norm, product_id)
    await db.flush()
    if purchase.status == "committed" and not ignore:
        await _emit_for_line(db, user, purchase, line)
    await db.commit()
    return await get_purchase(db, purchase_id)


def attached(purchase: Purchase, line: PurchaseLine, kind: str) -> list[PurchaseLine]:
    return [
        other
        for other in purchase.lines
        if other.parent_line_id == line.id and other.line_kind == kind
    ]


def observation_price(purchase: Purchase, line: PurchaseLine) -> tuple[Decimal, bool]:
    """Line total less attached discounts; deposits and tax never count."""
    discounts = attached(purchase, line, "discount")
    price = line.line_total - sum((abs(d.line_total) for d in discounts), Decimal("0"))
    if price < 0:
        price = Decimal("0")
    return price, bool(discounts)


async def _emit_for_line(
    db: AsyncSession, user: AppUser, purchase: Purchase, line: PurchaseLine
) -> None:
    price, promo = observation_price(purchase, line)
    await pricebook.observe(
        db,
        product_id=line.product_id,
        vendor_location_id=purchase.vendor_location_id,
        price=price,
        qty=line.qty if line.qty is not None else Decimal("1"),
        unit=line.unit or "each",
        source=purchase.source if purchase.source in ("receipt", "import") else "receipt",
        entered_by=user,
        is_promo=promo,
        observed_at=purchase.purchased_at,
        purchase_line_id=line.id,
    )


def _resolved_item(line: PurchaseLine) -> bool:
    return (
        line.line_kind == "item"
        and line.product_id is not None
        and line.resolution not in ("unmatched", "ignored")
    )


async def commit_purchase(db: AsyncSession, user: AppUser, purchase_id: uuid.UUID) -> Purchase:
    """Commit at any time. Resolved item lines emit observations; unresolved lines
    join the to-identify queue. On recommit, only changed lines void and re-emit."""
    purchase = await get_purchase(db, purchase_id, lock=True)
    ensure_not_voided(purchase)
    if purchase.vendor_location_id is None:
        raise ApiError(409, "location_required", "Choose the vendor location before committing.")
    live = await live_observations(db, purchase)
    live_rows = {}
    if live:
        rows = (
            (
                await db.execute(
                    select(PriceObservation).where(PriceObservation.id.in_(list(live.values())))
                )
            )
            .unique()
            .scalars()
        )
        live_rows = {o.purchase_line_id: o for o in rows}
    for line in purchase.lines:
        current = live_rows.get(line.id)
        if _resolved_item(line):
            price, promo = observation_price(purchase, line)
            if current is not None and (
                current.product_id == line.product_id
                and current.price == price
                and current.qty == (line.qty if line.qty is not None else Decimal("1"))
                and current.unit == (line.unit or "each")
                and current.is_promo == promo
                and current.vendor_location_id == purchase.vendor_location_id
            ):
                continue
            if current is not None:
                await pricebook.void(db, current.id, "line changed on recommit", user)
            await _emit_for_line(db, user, purchase, line)
        elif current is not None:
            await pricebook.void(db, current.id, "line no longer resolved on recommit", user)
    purchase.status = "committed"
    # The receipt's job is finished too; left at needs_review, the Receipts page
    # kept offering "ready to review" for a purchase already in the price book.
    await db.execute(
        update(IngestJob)
        .where(IngestJob.purchase_id == purchase.id, IngestJob.status == "needs_review")
        .values(status="done", stage="committed")
    )
    await db.commit()
    return await get_purchase(db, purchase_id)


async def reopen_purchase(db: AsyncSession, purchase_id: uuid.UUID) -> Purchase:
    purchase = await get_purchase(db, purchase_id, lock=True)
    if purchase.status != "committed":
        raise ApiError(409, "not_committed", "Only a committed purchase can be reopened.")
    purchase.status = "reviewed"
    await db.commit()
    return await get_purchase(db, purchase_id)


# --- the to-identify queue -------------------------------------------------------


async def to_identify(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT v.id AS vendor_id, v.name AS vendor_name, pl.raw_text_norm,
                   count(*) AS line_count,
                   json_agg(json_build_object(
                       'line_id', pl.id, 'purchase_id', p.id, 'raw_text', pl.raw_text,
                       'purchased_at', p.purchased_at, 'line_total', pl.line_total::text,
                       'qty', pl.qty::text, 'unit', pl.unit
                   ) ORDER BY p.purchased_at DESC) AS lines
            FROM purchase_line pl
            JOIN purchase p ON p.id = pl.purchase_id
            JOIN vendor_location vl ON vl.id = p.vendor_location_id
            JOIN vendor v ON v.id = vl.vendor_id
            WHERE p.status = 'committed' AND pl.line_kind = 'item'
              AND pl.resolution = 'unmatched' AND pl.removed_at IS NULL
            GROUP BY v.id, v.name, pl.raw_text_norm
            ORDER BY max(p.purchased_at) DESC
            """
        )
    )
    return [dict(r) for r in rows.mappings()]


def _queued(vendor_id: uuid.UUID, raw_text_norm: str):
    return (
        select(PurchaseLine.id, PurchaseLine.purchase_id)
        .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
        .join(VendorLocation, VendorLocation.id == Purchase.vendor_location_id)
        .where(
            Purchase.status == "committed",
            PurchaseLine.line_kind == "item",
            PurchaseLine.resolution == "unmatched",
            PurchaseLine.raw_text_norm == raw_text_norm,
            PurchaseLine.removed_at.is_(None),
            VendorLocation.vendor_id == vendor_id,
        )
    )


async def queued_line_ids(
    db: AsyncSession, vendor_id: uuid.UUID, raw_text_norm: str
) -> list[uuid.UUID]:
    """The group's lines still waiting in the to-identify queue."""
    return [lid for lid, _ in (await db.execute(_queued(vendor_id, raw_text_norm))).all()]


async def apply_to_identify(
    db: AsyncSession,
    user: AppUser,
    *,
    vendor_id: uuid.UUID,
    raw_text_norm: str,
    product_id: uuid.UUID | None,
    ignore: bool,
    line_ids: list[uuid.UUID] | None,
) -> int:
    """Identify one queued line and, unless narrowed, every other queued line with
    the same vendor and normalized text. Each emits an observation dated to its purchase."""
    rows = (await db.execute(_queued(vendor_id, raw_text_norm))).all()
    targets = [(lid, pid) for lid, pid in rows if line_ids is None or lid in set(line_ids)]
    for line_id, purchase_id in targets:
        await decide_line(db, user, purchase_id, line_id, product_id=product_id, ignore=ignore)
    return len(targets)


async def void_for_purchase(db: AsyncSession, purchase: Purchase) -> list[uuid.UUID]:
    live = await live_observations(db, purchase)
    return list(live.values())


__all__ = [
    "PriceObservationVoid",
    "apply_to_identify",
    "queued_line_ids",
    "commit_purchase",
    "decide_line",
    "reopen_purchase",
    "resolve_line",
    "resolve_purchase",
    "set_ranker",
    "to_identify",
    "upsert_alias",
]
