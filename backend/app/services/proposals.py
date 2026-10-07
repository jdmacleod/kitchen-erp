"""Product captures and proposals (04, 2L): evidence in, a person's decision out.

```
capture ─▶ dedupe (same payload, proposal still pending → the same one back)
        ─▶ merge candidates (app.catalog.proposals) ─▶ match the catalog
        ─▶ write_proposal: lock, supersede, write, retry once
accept  ─▶ lock ─▶ product, identifiers, listing, photos, main photo, stock check,
           one posted price ─▶ close ─▶ purge page text          (one transaction)
```

Nothing becomes a product, identifier, listing, photo or price without a
person's accept. Any failure during accept rolls everything back and leaves the
proposal pending.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import proposals as merging
from app.catalog.identifiers import classify_barcode
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.models import (
    AppUser,
    Ingredient,
    Product,
    ProductCapture,
    ProductIdentifier,
    ProductImage,
    ProductJob,
    ProductProposal,
    ProductStageResult,
    VendorListing,
)
from app.models.geo import Vendor, VendorLocation, point_expr
from app.services import pricebook, product_photos
from app.services.catalog import search_products

log = get_logger(__name__)

SHORTLIST = 8
STOCK_PHOTO_PRODUCTS = 3
IDENTIFIER_SOURCE = {
    "person": "manual",
    "scan": "barcode_scan",
    "manufacturer": "manufacturer",
    "usda_branded": "manufacturer",
    "page_data": "listing",
    "page_meta": "listing",
    "adapter": "listing",
    "model": "manual",
    "address": "listing",
}

# Test seam for the race tests (criterion 76): awaited at named points, a no-op otherwise.
PausePoint = Callable[[str], Awaitable[None]]


async def _no_pause(_: str) -> None:
    return None


pause: PausePoint = _no_pause


# --- captures -----------------------------------------------------------------------


def capture_sha(channel: str, source_url: str | None, payload: dict[str, Any]) -> str:
    """The capture's identity: its channel, address and payload, never its capture time."""
    canonical = json.dumps(
        {"channel": channel, "source_url": source_url, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class Evidence:
    """What a channel read from a capture, ready to merge."""

    candidates: list[merging.Candidate] = field(default_factory=list)
    listing: dict[str, Any] | None = None
    price: dict[str, Any] | None = None


@dataclass
class CaptureResult:
    capture: ProductCapture
    proposal: ProductProposal
    created: bool


async def _pending_proposal_for(db: AsyncSession, digest: str) -> ProductProposal | None:
    return (
        await db.execute(
            select(ProductProposal)
            .join(ProductCapture, ProductCapture.id == ProductProposal.capture_id)
            .where(ProductCapture.sha256 == digest, ProductProposal.status == "pending")
            .order_by(ProductProposal.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def create_capture(
    db: AsyncSession,
    *,
    user: AppUser,
    channel: str,
    payload: dict[str, Any],
    evidence: Evidence,
    source_url: str | None = None,
    captured_at: datetime | None = None,
    lat: Decimal | None = None,
    lon: Decimal | None = None,
    commit: bool = True,
) -> CaptureResult:
    """Record a capture and its proposal, or return the pending one it repeats (criterion 77)."""
    digest = capture_sha(channel, source_url, payload)
    existing = await _pending_proposal_for(db, digest)
    if existing is not None:
        capture = await db.get(ProductCapture, existing.capture_id)
        assert capture is not None
        return CaptureResult(capture, existing, created=False)
    capture = ProductCapture(
        id=new_id(),
        sha256=digest,
        channel=channel,
        source_url=source_url,
        payload=payload,
        captured_at=captured_at or datetime.now(UTC),
        capture_geo=point_expr(lat, lon) if lat is not None and lon is not None else None,
        created_by=user.id,
    )
    db.add(capture)
    await db.flush()
    fields = merging.merge(evidence.candidates)
    proposal = ProductProposal(
        id=new_id(),
        capture_id=capture.id,
        kind="new_product",
        status="pending",
        fields=fields,
        match=await match_catalog(db, fields, evidence.listing),
        listing=evidence.listing,
        price=evidence.price,
    )
    await write_proposal(db, proposal)
    if commit:
        await db.commit()
        await db.refresh(proposal)
    return CaptureResult(capture, proposal, created=True)


# --- one pending proposal per page or barcode (PR5) ----------------------------------


def listing_key(listing: dict[str, Any] | None) -> str | None:
    if not listing:
        return None
    return f"{listing['vendor_id']} {listing['canonical_url']}"


def gtin_key(fields: dict[str, Any], listing: dict[str, Any] | None) -> str | None:
    return None if listing else merging.value(fields, "gtin")


async def _supersede_others(
    db: AsyncSession,
    proposal: ProductProposal,
    fields: dict[str, Any],
    listing: dict[str, Any] | None,
) -> list[uuid.UUID]:
    keys = (listing_key(listing), gtin_key(fields, listing))
    conditions = []
    if keys[0] is not None:
        conditions.append(ProductProposal.listing_key == keys[0])
    if keys[1] is not None:
        conditions.append(ProductProposal.gtin_key == keys[1])
    if not conditions:
        return []
    others = list(
        (
            await db.execute(
                select(ProductProposal)
                .where(
                    ProductProposal.status == "pending",
                    ProductProposal.id != proposal.id,
                    or_(*conditions),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    await pause("supersede:locked")
    now = datetime.now(UTC)
    superseded = []
    for other in others:
        # Re-read under the lock: an accept may have decided it while we waited.
        if other.status != "pending":
            continue
        other.status = "superseded"
        other.decided_at = now
        other.result = {"superseded_by": str(proposal.id)}
        superseded.append(other.id)
    return superseded


async def write_proposal(
    db: AsyncSession, proposal: ProductProposal, **changes: Any
) -> list[uuid.UUID]:
    """Every write that can change a proposal's keys goes through here (PR5).

    Lock the pending proposals with the same page or GTIN, supersede them, then
    apply ``changes`` and write this one, retrying once if a concurrent writer
    got in first. The changes are applied only after the others are superseded:
    the new keys must not reach the table before then.
    """
    fields = changes.get("fields", proposal.fields)
    listing = changes.get("listing", proposal.listing)
    for attempt in (1, 2):
        try:
            async with db.begin_nested():
                with db.no_autoflush:
                    superseded = await _supersede_others(db, proposal, fields, listing)
                for name, value in changes.items():
                    setattr(proposal, name, value)
                db.add(proposal)
                await db.flush()
            await db.refresh(proposal, ["listing_key", "gtin_key"])
            return superseded
        except IntegrityError:
            if attempt == 2:
                raise ApiError(
                    409, "proposal_conflict", "Another capture of this product is being saved."
                ) from None
            log.info("proposal write retried", extra={"proposal_id": str(proposal.id)})
    return []


# --- matching -------------------------------------------------------------------------


async def _identifier_owner(
    db: AsyncSession, scheme: str, value: str, vendor_id: uuid.UUID | None = None
) -> uuid.UUID | None:
    stmt = select(ProductIdentifier.product_id).where(
        ProductIdentifier.scheme == scheme, ProductIdentifier.value == value
    )
    stmt = stmt.where(
        ProductIdentifier.vendor_id == vendor_id
        if vendor_id is not None
        else ProductIdentifier.vendor_id.is_(None)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def match_catalog(
    db: AsyncSession, fields: dict[str, Any], listing: dict[str, Any] | None
) -> dict[str, Any]:
    """A strong match by identifier or known listing, else up to eight fuzzy candidates."""
    strong = None
    gtin = merging.value(fields, "gtin")
    if gtin and (owner := await _identifier_owner(db, "gtin", gtin)):
        strong = {"product_id": str(owner), "reason": "identifier"}
    if strong is None and listing:
        known = (
            await db.execute(
                select(VendorListing.product_id).where(
                    VendorListing.vendor_id == uuid.UUID(str(listing["vendor_id"])),
                    VendorListing.canonical_url == listing["canonical_url"],
                    VendorListing.product_id.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if known is not None:
            strong = {"product_id": str(known), "reason": "listing"}
        elif (sku := merging.value(fields, "item_number")) and (
            owner := await _identifier_owner(
                db, "vendor_sku", str(sku), uuid.UUID(str(listing["vendor_id"]))
            )
        ):
            strong = {"product_id": str(owner), "reason": "identifier"}
    # The title alone, and with the brand: a household's product often has no brand.
    title, brand = merging.value(fields, "title"), merging.value(fields, "brand")
    queries = [str(title)] if title else []
    if title and brand:
        queries.append(f"{brand} {title}")
    best: dict[str, dict[str, Any]] = {}
    for query in queries:
        for hit in await search_products(db, query, SHORTLIST):
            entry = {
                "product_id": str(hit.id),
                "name": hit.name,
                "brand": hit.brand,
                "score": str(hit.score),
            }
            seen = best.get(entry["product_id"])
            if seen is None or Decimal(seen["score"]) < Decimal(entry["score"]):
                best[entry["product_id"]] = entry
    candidates = sorted(best.values(), key=lambda c: -Decimal(c["score"]))[:SHORTLIST]
    return {
        "strong": strong,
        "candidates": candidates,
        # A strong match preselects "Update"; a fuzzy one preselects nothing (criterion 74).
        "preselect": f"update:{strong['product_id']}" if strong else None,
    }


def apply_model_pick(match: dict[str, Any], pick: str | None) -> dict[str, Any]:
    """Record the local model's pick: "new" or a shortlisted id; anything else is rejected."""
    shortlist = {c["product_id"] for c in match.get("candidates", [])}
    out = dict(match)
    if pick == "new" or pick in shortlist:
        out["model"] = {"pick": pick}
    else:
        out["model"] = {"rejected": pick}
    return out


# --- reading -------------------------------------------------------------------------


async def get_proposal(db: AsyncSession, proposal_id: uuid.UUID, *, lock: bool = False):
    stmt = (
        select(ProductProposal)
        .where(ProductProposal.id == proposal_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        stmt = stmt.with_for_update()
    proposal = (await db.execute(stmt)).scalar_one_or_none()
    if proposal is None:
        raise ApiError(404, "not_found", "No such proposal.")
    return proposal


async def photos_of(db: AsyncSession, proposal_id: uuid.UUID) -> list[ProductImage]:
    return list(
        (
            await db.execute(
                select(ProductImage)
                .where(ProductImage.proposal_id == proposal_id)
                .order_by(ProductImage.created_at, ProductImage.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def jobs_of(db: AsyncSession, proposal: ProductProposal) -> list[ProductJob]:
    image_ids = select(ProductImage.id).where(ProductImage.proposal_id == proposal.id)
    conditions = [ProductJob.product_image_id.in_(image_ids)]
    if proposal.capture_id is not None:
        conditions.append(ProductJob.product_capture_id == proposal.capture_id)
    return list(
        (
            await db.execute(
                select(ProductJob).where(or_(*conditions)).order_by(ProductJob.created_at)
            )
        ).scalars()
    )


async def vendor_context(db: AsyncSession, proposal: ProductProposal) -> dict[str, Any] | None:
    """The page's vendor, its stores, and the one to preselect for a posted price.

    The preselected store is the household's most recently used location of
    that vendor (04, 2L), or its only active location; with neither, the review
    records no price by default.
    """
    if not proposal.listing:
        return None
    vendor = await db.get(Vendor, uuid.UUID(str(proposal.listing["vendor_id"])))
    if vendor is None:
        return None
    locations = (
        await db.execute(
            select(VendorLocation.id, VendorLocation.name)
            .where(VendorLocation.vendor_id == vendor.id, VendorLocation.active)
            .order_by(VendorLocation.name)
        )
    ).all()
    suggested = (
        await db.execute(
            text(
                """
                SELECT p.vendor_location_id FROM purchase p
                JOIN vendor_location vl ON vl.id = p.vendor_location_id
                WHERE vl.vendor_id = :vendor AND vl.active AND p.status = 'committed'
                ORDER BY p.purchased_at DESC LIMIT 1
                """
            ),
            {"vendor": vendor.id},
        )
    ).scalar_one_or_none()
    if suggested is None and len(locations) == 1:
        suggested = locations[0].id  # one store: no choice to make
    return {
        "id": vendor.id,
        "name": vendor.name,
        "price_scope": vendor.price_scope,
        "locations": [{"id": i, "name": n} for i, n in locations],
        "suggested_location_id": suggested,
    }


async def reading_of(db: AsyncSession, proposal: ProductProposal) -> dict[str, Any] | None:
    """Which path identified the capture's photos, and any error (criterion 81)."""
    if proposal.capture_id is None:
        return None
    output = (
        await db.execute(
            select(ProductStageResult.output)
            .join(ProductJob, ProductJob.id == ProductStageResult.job_id)
            .where(
                ProductJob.product_capture_id == proposal.capture_id,
                ProductStageResult.stage.in_(("identify", "extract")),
                ProductStageResult.output.has_key("path"),
            )
            .order_by(ProductStageResult.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if output is None:
        return None
    return {"path": output["path"], "error": output.get("error")}


async def list_pending(db: AsyncSession, limit: int = 50) -> list[ProductProposal]:
    return list(
        (
            await db.execute(
                select(ProductProposal)
                .where(ProductProposal.status == "pending")
                .order_by(ProductProposal.created_at, ProductProposal.id)
                .limit(limit)
            )
        ).scalars()
    )


async def pending_counts(db: AsyncSession) -> dict[str, int]:
    rows = await db.execute(
        select(ProductProposal.kind, func.count())
        .where(ProductProposal.status == "pending")
        .group_by(ProductProposal.kind)
    )
    return {kind: int(n) for kind, n in rows.all()}


# --- the person's edits ---------------------------------------------------------------


def normalize_edits(edits: dict[str, Any]) -> dict[str, Any]:
    """A person's values in the stored shape: a GTIN as GTIN-14, a pack as qty and unit."""
    out = dict(edits)
    if out.get("gtin") is not None:
        try:
            scheme, value = classify_barcode(str(out["gtin"]))
        except ValueError:
            raise ApiError(422, "invalid_gtin", "That barcode's check digit is wrong.") from None
        if scheme != "gtin":
            raise ApiError(422, "invalid_gtin", "That is not a barcode number.")
        out["gtin"] = value
    if out.get("pack") is not None:
        pack = out["pack"]
        try:
            qty = Decimal(str(pack["qty"]))
            unit = str(pack["unit"])
        except (KeyError, TypeError, InvalidOperation):
            raise ApiError(422, "invalid_pack", "A pack is a quantity and a unit.") from None
        if qty <= 0 or unit not in merging._UNITS:
            raise ApiError(422, "invalid_pack", "A pack is a positive quantity and a known unit.")
        out["pack"] = {"qty": format(qty, "f"), "unit": unit}
    if out.get("pieces") is not None:
        pieces = merging.pieces_value(out["pieces"])
        if pieces is None:
            raise ApiError(422, "invalid_pieces", "Pieces are a whole number, like 5 links.")
        out["pieces"] = pieces
    return out


def _pending(proposal: ProductProposal) -> None:
    if proposal.status != "pending":
        raise ApiError(
            409,
            "proposal_not_pending",
            "This proposal has already been decided.",
            {"status": proposal.status},
        )


async def edit(db: AsyncSession, proposal_id: uuid.UUID, edits: dict[str, Any]) -> ProductProposal:
    """Apply a person's field edits; a new GTIN may supersede another pending proposal."""
    proposal = await get_proposal(db, proposal_id, lock=True)
    _pending(proposal)
    try:
        fields = merging.with_person(proposal.fields, normalize_edits(edits))
    except merging.UnknownCandidate as exc:
        raise ApiError(422, "validation_error", str(exc)) from None
    match = await match_catalog(db, fields, proposal.listing)
    await write_proposal(db, proposal, fields=fields, match=match)
    await db.commit()
    return await get_proposal(db, proposal_id)


# --- accept and reject ----------------------------------------------------------------


@dataclass
class AcceptInput:
    action: str  # "new" or "update"
    product_id: uuid.UUID | None = None
    ingredient_id: uuid.UUID | None = None
    kind: str | None = None
    edits: dict[str, Any] = field(default_factory=dict)
    record_price: bool = False
    # The reviewer's price {amount, qty, unit}, over the page's.
    price: dict[str, Any] | None = None
    vendor_location_id: uuid.UUID | None = None
    # The reviewer's photo choices: the main photo, each photo's role, and ones to hide.
    main_photo_id: uuid.UUID | None = None
    photo_roles: dict[uuid.UUID, str] = field(default_factory=dict)
    hidden_photo_ids: list[uuid.UUID] = field(default_factory=list)


def _purge(capture: ProductCapture | None) -> None:
    """Drop the page text once the proposal is decided (PV12); nothing else may change."""
    if capture is not None and "dom_text" in capture.payload:
        capture.payload = {k: v for k, v in capture.payload.items() if k != "dom_text"}


def _pack(fields: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    pack = merging.value(fields, "pack")
    if not pack:
        return None, None
    try:
        return Decimal(str(pack["qty"])), str(pack["unit"])
    except (KeyError, TypeError, InvalidOperation):
        raise ApiError(422, "invalid_pack", "The pack size is not a quantity and unit.") from None


def _source_note(fields: dict[str, Any], name: str, now: datetime) -> dict[str, Any] | None:
    state = fields.get(name)
    if not state:
        return None
    return {"source": state["source"], "checked_at": now.isoformat()}


async def _apply_fields(
    product: Product, fields: dict[str, Any], *, overwrite: bool, now: datetime
) -> bool:
    """Set the product's brand, name, pack and pieces from the fields; a reviewer's value
    always wins. True when the pack or its pieces changed, so its prices are recomputed."""
    before = (product.pack_qty, product.pack_unit, product.pack_count)
    sources = dict(product.field_source or {})
    for name, attr in (("brand", "brand"), ("title", "name")):
        state = fields.get(name)
        if not state or not str(state["value"]).strip():
            continue
        if overwrite or state["source"] == "person" or not getattr(product, attr):
            setattr(product, attr, str(state["value"]).strip())
            sources[attr] = _source_note(fields, name, now)
    qty, unit = _pack(fields)
    if qty is not None and (
        overwrite or fields["pack"]["source"] == "person" or not product.pack_qty
    ):
        product.pack_qty, product.pack_unit = qty, unit
        sources["pack"] = _source_note(fields, "pack", now)
    pieces = merging.value(fields, "pieces")
    if pieces and (overwrite or fields["pieces"]["source"] == "person" or not product.pack_count):
        if _holds_pieces(product.pack_unit):
            product.pack_count = int(pieces["count"])
            product.piece_name = pieces.get("name")
            sources["pieces"] = _source_note(fields, "pieces", now)
        elif fields["pieces"]["source"] == "person":
            raise ApiError(422, "pieces_need_size", "Pieces go with a pack's weight or volume.")
    elif product.pack_count and not _holds_pieces(product.pack_unit):
        # A new pack counted in pieces says how many itself.
        product.pack_count, product.piece_name = None, None
    product.field_source = sources
    return (product.pack_qty, product.pack_unit, product.pack_count) != before


def _holds_pieces(unit: str | None) -> bool:
    """Pieces go with a mass or volume pack; a count pack already says how many."""
    known = merging._UNITS.get(unit or "")
    return known is not None and known.dimension in ("mass", "volume")


async def _add_identifier(
    db: AsyncSession,
    product: Product,
    scheme: str,
    value: str,
    source: str,
    vendor_id: uuid.UUID | None = None,
) -> bool:
    """Give the product a code; 409 identifier_taken when another product holds it (PR6)."""
    owner = await _identifier_owner(db, scheme, value, vendor_id)
    if owner == product.id:
        return False
    if owner is not None:
        other = await db.get(Product, owner)
        raise ApiError(
            409,
            "identifier_taken",
            f"Another product, {other.name if other else 'unknown'}, has that code.",
            {"product_id": str(owner), "name": other.name if other else None},
        )
    db.add(
        ProductIdentifier(
            product_id=product.id, scheme=scheme, value=value, source=source, vendor_id=vendor_id
        )
    )
    await db.flush()
    return True


async def _stock_check(db: AsyncSession, image: ProductImage) -> None:
    """A vendor-page photo that also appears on three or more other products of that vendor."""
    if image.source_kind != "vendor_listing" or image.phash is None or image.vendor_id is None:
        return
    others = (
        await db.execute(
            text(
                """
                SELECT count(DISTINCT product_id) FROM product_image
                WHERE vendor_id = :vendor AND phash = :phash AND product_id IS NOT NULL
                  AND product_id <> :product
                """
            ),
            {"vendor": image.vendor_id, "phash": image.phash, "product": image.product_id},
        )
    ).scalar_one()
    image.is_stock_suspect = int(others) >= STOCK_PHOTO_PRODUCTS


async def _attach_photos(
    db: AsyncSession, proposal: ProductProposal, product: Product, data: AcceptInput
) -> int:
    attached = 0
    existing = set(
        (
            await db.execute(
                select(ProductImage.upload_sha256).where(ProductImage.product_id == product.id)
            )
        ).scalars()
    )
    for image in await photos_of(db, proposal.id):
        if image.product_id is not None:
            continue
        if image.upload_sha256 in existing:
            continue  # the product already has this photo: keep the one it has
        image.product_id = product.id
        image.role = data.photo_roles.get(image.id, image.role)
        if image.id in data.hidden_photo_ids and image.status in ("candidate", "active"):
            image.status = "hidden"
        elif image.status == "candidate":
            image.status = "active"
        if image.id == data.main_photo_id and image.role == "product":
            # The reviewer's choice wins, as "Use as main photo" does (1I).
            await db.execute(
                update(ProductImage)
                .where(ProductImage.product_id == product.id, ProductImage.pinned)
                .values(pinned=False, pinned_at=None)
            )
            image.pinned, image.pinned_at = True, datetime.now(UTC)
        await db.flush()
        await _stock_check(db, image)
        attached += 1
    return attached


async def _upsert_listing(
    db: AsyncSession, proposal: ProductProposal, product: Product, now: datetime
) -> VendorListing | None:
    listing = proposal.listing
    if not listing:
        return None
    vendor_id = uuid.UUID(str(listing["vendor_id"]))
    row = (
        await db.execute(
            select(VendorListing)
            .where(
                VendorListing.vendor_id == vendor_id,
                VendorListing.canonical_url == listing["canonical_url"],
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = VendorListing(
            id=new_id(), vendor_id=vendor_id, canonical_url=listing["canonical_url"]
        )
        db.add(row)
    row.product_id = product.id
    # The merged fields hold the reviewer's edits; the listing was written at capture.
    row.title = str(merging.value(proposal.fields, "title") or product.name)
    row.vendor_sku = merging.value(proposal.fields, "item_number") or listing.get("vendor_sku")
    row.store_ref = listing.get("store_ref")
    row.last_captured_at = now
    row.status = "active"
    await db.flush()
    return row


async def _record_price(
    db: AsyncSession,
    proposal: ProductProposal,
    product: Product,
    listing: VendorListing | None,
    data: AcceptInput,
    user: AppUser,
) -> uuid.UUID | None:
    """At most one posted price, at the location the reviewer confirmed."""
    if not data.record_price or listing is None:
        return None
    if not proposal.price and data.price is None:
        return None
    if data.vendor_location_id is None:
        raise ApiError(422, "location_required", "Say which store this posted price is for.")
    location = await db.get(VendorLocation, data.vendor_location_id)
    if location is None or location.vendor_id != listing.vendor_id:
        raise ApiError(
            422, "location_mismatch", "That store is not one of this page's vendor's stores."
        )
    if data.price is not None:
        given = {k: str(v) for k, v in data.price.items()}
        basis = merging.price_basis(given)
        if basis is None:
            raise ApiError(422, "unknown_unit", f"There is no unit {given.get('unit')!r}.")
    elif proposal.fields.get("price", {}).get("conflict"):
        raise ApiError(
            409,
            "price_conflict",
            "The page gives this price per different quantities. Say what it is for.",
        )
    else:
        basis = proposal.price
    observation = await pricebook.observe(
        db,
        product_id=product.id,
        vendor_location_id=location.id,
        price=Decimal(str(basis["amount"])),
        qty=Decimal(str(basis.get("qty", "1"))),
        unit=str(basis.get("unit", "each")),
        is_promo=bool((proposal.price or {}).get("is_promo", False)),
        source="listing",
        listing_id=listing.id,
        entered_by=user,
    )
    return observation.id


async def _product_for(
    db: AsyncSession, proposal: ProductProposal, data: AcceptInput, now: datetime
) -> tuple[Product, bool]:
    fields = proposal.fields
    if data.action == "update":
        target = data.product_id or (
            uuid.UUID(proposal.match["strong"]["product_id"])
            if proposal.match.get("strong")
            else proposal.product_id
        )
        if target is None:
            raise ApiError(422, "product_required", "Say which product to update.")
        product = await product_photos.lock_product(db, target)
        if await _apply_fields(product, fields, overwrite=False, now=now):
            await db.flush()
            # The pack or its pieces changed: what was recorded is priced again.
            await pricebook.recompute_for_product_core(db, product.id)
        return product, False
    if data.ingredient_id is None:
        raise ApiError(422, "ingredient_required", "Choose the ingredient this product is.")
    if await db.get(Ingredient, data.ingredient_id) is None:
        raise ApiError(404, "not_found", "No such ingredient.")
    name = str(merging.value(fields, "title") or "").strip()
    if not name:
        raise ApiError(422, "name_required", "Give the product a name.")
    brand = merging.value(fields, "brand")
    gtin = merging.value(fields, "gtin")
    product = Product(
        id=new_id(),
        ingredient_id=data.ingredient_id,
        name=name,
        brand=str(brand).strip() if brand else None,
        kind=data.kind or ("branded" if (brand or gtin) else "loose"),
        attributes={},
        field_source={},
    )
    db.add(product)
    await _apply_fields(product, fields, overwrite=True, now=now)
    await db.flush()
    return product, True


async def accept(
    db: AsyncSession, user: AppUser, proposal_id: uuid.UUID, data: AcceptInput
) -> ProductProposal:
    """Accept a proposal in one transaction; on any failure nothing is kept (criterion 75)."""
    try:
        proposal = await get_proposal(db, proposal_id, lock=True)
        await pause("accept:locked")
        _pending(proposal)
        if data.edits:
            try:
                fields = merging.with_person(proposal.fields, normalize_edits(data.edits))
            except merging.UnknownCandidate as exc:
                raise ApiError(422, "validation_error", str(exc)) from None
            # A GTIN set here may supersede another pending proposal, like any edit.
            await write_proposal(db, proposal, fields=fields)
        if conflicts := merging.has_conflict(proposal.fields):
            raise ApiError(
                409,
                "unresolved_conflict",
                "Choose a value for each conflicting field first.",
                {"fields": conflicts},
            )
        now = datetime.now(UTC)
        product, created = await _product_for(db, proposal, data, now)
        fields = proposal.fields
        identifiers = []
        if gtin := merging.value(fields, "gtin"):
            scheme, value = classify_barcode(str(gtin))
            source = IDENTIFIER_SOURCE[fields["gtin"]["source"]]
            if await _add_identifier(db, product, scheme, value, source):
                identifiers.append(value)
        listing = await _upsert_listing(db, proposal, product, now)
        sku = merging.value(fields, "item_number") if listing is not None else None
        if sku and await _add_identifier(
            db,
            product,
            "vendor_sku",
            str(sku),
            "listing",
            listing.vendor_id,  # type: ignore[union-attr]
        ):
            identifiers.append(str(sku))
        attached = await _attach_photos(db, proposal, product, data)
        await product_photos.reselect(db, product)
        observation_id = await _record_price(db, proposal, product, listing, data, user)
        proposal.status = "accepted"
        proposal.product_id = product.id
        proposal.decided_at = now
        proposal.decided_by = user.id
        proposal.result = {
            "product_id": str(product.id),
            "created": created,
            "identifiers": identifiers,
            "listing_id": str(listing.id) if listing else None,
            "photos": attached,
            "observation_id": str(observation_id) if observation_id else None,
        }
        _purge(await db.get(ProductCapture, proposal.capture_id) if proposal.capture_id else None)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return await get_proposal(db, proposal_id)


async def reject(db: AsyncSession, user: AppUser, proposal_id: uuid.UUID) -> ProductProposal:
    proposal = await get_proposal(db, proposal_id, lock=True)
    _pending(proposal)
    proposal.status = "rejected"
    proposal.decided_at = datetime.now(UTC)
    proposal.decided_by = user.id
    _purge(await db.get(ProductCapture, proposal.capture_id) if proposal.capture_id else None)
    await db.commit()
    return await get_proposal(db, proposal_id)


# --- photographing a product (2L) ------------------------------------------------------


async def capture_photos(
    db: AsyncSession,
    user: AppUser,
    uploads: list[product_photos.PhotoUpload],
    *,
    captured_at: datetime | None = None,
    lat: Decimal | None = None,
    lon: Decimal | None = None,
) -> CaptureResult:
    """Up to four photos become one proposal, and an ``identify`` job reads them.

    Every photo is checked before anything is stored. The same photos again,
    while their proposal is pending, return that proposal.
    """
    product_photos.check_uploads(uploads)
    payload = {
        "photos": [{"sha256": hashlib.sha256(u.data).hexdigest(), "role": u.role} for u in uploads]
    }
    result = await create_capture(
        db,
        user=user,
        channel="photo",
        payload=payload,
        evidence=Evidence(),
        captured_at=captured_at,
        lat=lat,
        lon=lon,
        commit=False,
    )
    if result.created:
        await product_photos.add_photos(
            db,
            None,
            uploads,
            proposal_id=result.proposal.id,
            captured_at=captured_at,
            commit=False,
        )
        # After the photos' own jobs, so a single worker prepares them first.
        db.add(ProductJob(id=new_id(), kind="identify", product_capture_id=result.capture.id))
    await db.commit()
    await db.refresh(result.proposal)
    return result
