"""Offer to remember a receipt's store code on its location (1F, criterion 61).

Receipt matching gives a location that prints its store number a decisive
match (``ingest/header.py``). Review offers to remember the number the header
read, but only when it looks like a store number: a loyalty or member number is
printed on receipts from every branch, and remembering one would send every
later receipt carrying that card to this branch (eng review R3).

A code is offered when all of these hold:
- the purchase came from a receipt, is still being reviewed, and has a location;
- the header read a store identifier, and the location does not already have it;
- the first line of the receipt text that prints it is in the top quarter of the
  receipt, and names no member, card, loyalty, rewards, account or phone;
- no other location of the same vendor already has it.

Receipt text is untrusted: it is only searched and shown, never interpreted.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.ingest.header import identifier_in_text
from app.ingest.stages import latest_results
from app.models.geo import VendorLocation
from app.models.purchases import IngestJob, Purchase
from app.services.purchases import get_purchase

MAX_CODE_LENGTH = 40
_NOT_A_STORE = re.compile(
    r"member|card|loyal|reward|account|acct|phone|\btel\b|\bph\b", re.IGNORECASE
)
_LONG_DIGITS = re.compile(r"\d{4,}")
MASK = "••••"


@dataclass(frozen=True)
class StoreCodeOffer:
    code: str
    location_id: uuid.UUID
    location_name: str
    printed_line: str  # the line the code was printed on, other long numbers masked


def store_line(code: str, text: str) -> str | None:
    """The line that prints ``code``, when it reads like a store number; else None."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    top = max(1, math.ceil(len(lines) / 4))
    for index, line in enumerate(lines):
        if identifier_in_text(code, line):
            if index >= top or _NOT_A_STORE.search(line):
                return None
            return line
    return None


def mask_line(line: str, code: str) -> str:
    """Hide every run of four or more digits except the code itself."""
    kept = code.lower()
    return _LONG_DIGITS.sub(lambda m: m.group(0) if m.group(0).lower() == kept else MASK, line)


def _has(location: VendorLocation, code: str) -> bool:
    return any(c.strip().lower() == code.lower() for c in location.receipt_identifiers)


async def _header_facts(db: AsyncSession, purchase: Purchase) -> tuple[str, str] | None:
    """The store identifier the header read and the receipt text, if both exist."""
    job = await db.scalar(select(IngestJob).where(IngestJob.purchase_id == purchase.id))
    if job is None:
        return None
    latest = await latest_results(db, job.id)
    header = latest.get("header")
    ocr = latest.get("ocr")
    code = header.output.get("store_identifier") if header is not None else None
    text = ocr.output.get("text") if ocr is not None else None
    if not isinstance(code, str) or not isinstance(text, str):
        return None
    code = code.strip()
    if not code or len(code) > MAX_CODE_LENGTH:
        return None
    return code, text


async def offer_for(db: AsyncSession, purchase: Purchase) -> StoreCodeOffer | None:
    if (
        purchase.source != "receipt"
        or purchase.status not in ("draft", "reviewed")
        or purchase.vendor_location_id is None
    ):
        return None
    facts = await _header_facts(db, purchase)
    if facts is None:
        return None
    code, text = facts
    result = await db.execute(
        select(VendorLocation).where(
            VendorLocation.vendor_id
            == select(VendorLocation.vendor_id)
            .where(VendorLocation.id == purchase.vendor_location_id)
            .scalar_subquery()
        )
    )
    branches = list(result.unique().scalars())
    location = next((b for b in branches if b.id == purchase.vendor_location_id), None)
    if location is None or any(_has(b, code) for b in branches):
        return None
    line = store_line(code, text)
    if line is None:
        return None
    return StoreCodeOffer(code, location.id, location.name, mask_line(line, code))


async def get_offer(db: AsyncSession, purchase_id: uuid.UUID) -> StoreCodeOffer | None:
    return await offer_for(db, await get_purchase(db, purchase_id))


async def remember(db: AsyncSession, purchase_id: uuid.UUID, code: str) -> StoreCodeOffer:
    """Append the offered code to the purchase's location. Nothing else is accepted."""
    purchase = await get_purchase(db, purchase_id, lock=True)
    offer = await offer_for(db, purchase)
    if offer is None or offer.code != code.strip():
        raise ApiError(
            409,
            "store_code_not_offered",
            "That store code is not on offer for this receipt any more; another location "
            "may have it, or the location changed.",
        )
    location = (
        (
            await db.execute(
                select(VendorLocation)
                .where(VendorLocation.id == offer.location_id)
                .with_for_update(of=VendorLocation)
                .execution_options(populate_existing=True)
            )
        )
        .unique()
        .scalar_one()
    )
    location.receipt_identifiers = [*location.receipt_identifiers, offer.code]
    await db.commit()
    return offer
