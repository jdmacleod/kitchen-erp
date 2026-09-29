"""Header stage: vendor, location, purchase time, and totals.

Field extraction is the language model constrained to :class:`ReceiptHeader`.
Location matching combines three kinds of evidence, each computed with bound
parameters (merchant text never reaches SQL as a fragment):

* an exact match between a location's ``receipt_identifiers`` and the store
  identifier the model read, or a token in the OCR text (weight 1.0);
* proximity of the document's ``capture_geo`` to a location, within
  ``INGEST_LOCATION_RADIUS_M`` (weight 0.7);
* trigram similarity between the printed merchant name and vendor names, at
  0.3 or better (weight 0.6 × similarity).

A single confident candidate is accepted; otherwise the ranked candidates are
recorded for the reviewer and the purchase keeps ``vendor_location_id`` null.

Stage output shape::

    {"parsed": true, "model_attempts": 1,
     "merchant_name": "...", "store_identifier": "...", "address_text": "...",
     "purchased_at_local": "2026-03-04 17:42", "purchased_at": "2026-03-05T01:42:00+00:00",
     "subtotal": "12.34", "tax": "0.00", "total": "12.34",
     "flags": [],                       # e.g. purchased_at_unparsed
     "location": {"matched": true, "vendor_location_id": "...", "vendor_id": "...",
                  "candidates": [{"vendor_location_id", "vendor_id", "vendor_name",
                                  "location_name", "score": "1.600",
                                  "evidence": {"identifier": "0412" | null,
                                               "distance_m": "42.0" | null,
                                               "name_similarity": "1.000" | null}}]}}

When the model's reply never validates: ``{"parsed": false, "reason":
"invalid_model_output", "model_attempts": 3, "location": {...}}``; matching still
runs on the OCR text and the job continues to ``lines`` with fallbacks.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.ingest.schemas import ReceiptHeader
from app.models import ReceiptDocument
from app.models.geo import Place, Vendor, VendorLocation

HEADER_TASK = (
    "Extract the receipt header: merchant name, store identifier (store number or "
    "code), address, phone, purchase date and time, subtotal, tax and total. Use only "
    "what is printed."
)

IDENTIFIER_WEIGHT = Decimal("1.0")
PROXIMITY_WEIGHT = Decimal("0.7")
SIMILARITY_WEIGHT = Decimal("0.6")
SIMILARITY_FLOOR = Decimal("0.3")
CONFIDENT_SCORE = Decimal("0.55")
CONFIDENT_GAP = Decimal("0.3")
MAX_CANDIDATES = 10

_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")
_DATE_FORMATS = ("%Y-%m-%d",)

# The model is asked for ISO but often echoes the till's own format (#58), and a
# till prints its date in one order. RECEIPT_DATE_ORDER says which, so "07/04/26"
# is read one way on every receipt and never guessed per receipt.
_DATE_ORDERS = {
    "MDY": ("%m{s}%d{s}%Y", "%m{s}%d{s}%y"),
    "DMY": ("%d{s}%m{s}%Y", "%d{s}%m{s}%y"),
    "YMD": ("%Y{s}%m{s}%d", "%y{s}%m{s}%d"),
}
_SEPARATORS = ("/", "-", ".")
_TIMES = ("", " %H:%M:%S", " %H:%M", " %I:%M:%S %p", " %I:%M %p", " %I:%M:%S%p", " %I:%M%p")


def _printed_formats(order: str) -> tuple[str, ...]:
    return tuple(
        date.format(s=sep) + time
        for date in _DATE_ORDERS[order]
        for sep in _SEPARATORS
        for time in _TIMES
    )


def parse_local_datetime(text: str | None, timezone: str, order: str = "MDY") -> datetime | None:
    """A printed local time (no zone) interpreted in the household zone, returned in UTC.

    ISO forms are always accepted. A printed date such as ``07/30/20 17:08`` is
    read in ``order`` (the deployment's RECEIPT_DATE_ORDER) and in no other.
    """
    if not text:
        return None
    value = " ".join(text.split())
    zone = ZoneInfo(timezone)
    for fmt in _DATETIME_FORMATS + _DATE_FORMATS + _printed_formats(order):
        try:
            local = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return local.replace(tzinfo=zone).astimezone(UTC)
    return None


@dataclass
class Candidate:
    vendor_location_id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str
    location_name: str
    identifier: str | None = None
    distance_m: Decimal | None = None
    name_similarity: Decimal | None = None

    @property
    def score(self) -> Decimal:
        score = Decimal("0")
        if self.identifier is not None:
            score += IDENTIFIER_WEIGHT
        if self.distance_m is not None:
            score += PROXIMITY_WEIGHT
        if self.name_similarity is not None:
            score += SIMILARITY_WEIGHT * self.name_similarity
        return score.quantize(Decimal("0.001"))

    def as_json(self) -> dict[str, Any]:
        return {
            "vendor_location_id": str(self.vendor_location_id),
            "vendor_id": str(self.vendor_id),
            "vendor_name": self.vendor_name,
            "location_name": self.location_name,
            "score": str(self.score),
            "evidence": {
                "identifier": self.identifier,
                "distance_m": None if self.distance_m is None else str(self.distance_m),
                "name_similarity": (
                    None if self.name_similarity is None else str(self.name_similarity)
                ),
            },
        }


@dataclass
class LocationMatch:
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def confident(self) -> Candidate | None:
        if not self.candidates:
            return None
        top = self.candidates[0]
        if top.score < CONFIDENT_SCORE:
            return None
        if len(self.candidates) > 1 and top.score - self.candidates[1].score < CONFIDENT_GAP:
            return None
        return top

    def as_json(self) -> dict[str, Any]:
        chosen = self.confident
        return {
            "matched": chosen is not None,
            "vendor_location_id": None if chosen is None else str(chosen.vendor_location_id),
            "vendor_id": None if chosen is None else str(chosen.vendor_id),
            "candidates": [c.as_json() for c in self.candidates[:MAX_CANDIDATES]],
        }


def identifier_in_text(identifier: str, text: str) -> bool:
    pattern = r"(?<![A-Za-z0-9])" + re.escape(identifier) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


async def match_location(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    receipt_text: str,
    merchant_name: str | None,
    store_identifier: str | None,
    radius_m: int | None = None,
) -> LocationMatch:
    radius = radius_m if radius_m is not None else get_settings().ingest_location_radius_m
    candidates: dict[uuid.UUID, Candidate] = {}

    def candidate(location: VendorLocation) -> Candidate:
        existing = candidates.get(location.id)
        if existing is None:
            existing = Candidate(
                vendor_location_id=location.id,
                vendor_id=location.vendor_id,
                vendor_name=location.vendor.name,
                location_name=location.name,
            )
            candidates[location.id] = existing
        return existing

    active = (
        select(VendorLocation)
        .join(Vendor, Vendor.id == VendorLocation.vendor_id)
        .where(VendorLocation.active.is_(True), Vendor.active.is_(True))
    )

    # 1. Receipt identifiers: exact against the model's store identifier, or a
    #    whole token of the OCR text.
    wanted = (store_identifier or "").strip().lower()
    with_identifiers = await db.execute(
        active.where(func.cardinality(VendorLocation.receipt_identifiers) > 0)
    )
    for location in with_identifiers.scalars().unique():
        for identifier in location.receipt_identifiers:
            ident = identifier.strip()
            if not ident:
                continue
            if (wanted and ident.lower() == wanted) or identifier_in_text(ident, receipt_text):
                candidate(location).identifier = ident
                break

    # 2. Proximity of the capture point.
    distance = cast(func.ST_Distance(Place.geom, ReceiptDocument.capture_geo), Numeric)
    near = (
        select(VendorLocation, func.round(distance, 1))
        .select_from(ReceiptDocument)
        .join(Place, func.ST_DWithin(Place.geom, ReceiptDocument.capture_geo, radius))
        .join(VendorLocation, VendorLocation.place_id == Place.id)
        .join(Vendor, Vendor.id == VendorLocation.vendor_id)
        .where(
            ReceiptDocument.id == document_id,
            ReceiptDocument.capture_geo.isnot(None),
            VendorLocation.active.is_(True),
            Vendor.active.is_(True),
        )
    )
    for location, metres in (await db.execute(near)).unique():
        candidate(location).distance_m = Decimal(str(metres))

    # 3. Trigram similarity of the printed merchant name to vendor names.
    merchant = (merchant_name or "").strip()
    if merchant:
        similarity = cast(func.similarity(Vendor.name, merchant), Numeric)
        similar = await db.execute(
            active.add_columns(func.round(similarity, 3)).where(similarity >= SIMILARITY_FLOOR)
        )
        for location, sim in similar.unique():
            value = Decimal(str(sim))
            if location.vendor.name.strip().lower() == merchant.lower():
                value = Decimal("1.000")
            candidate(location).name_similarity = value

    ranked = sorted(candidates.values(), key=lambda c: (-c.score, c.location_name))
    return LocationMatch(candidates=[c for c in ranked if c.score > 0])


# A total line as a till prints it: the label, anything (stars, a colon), then the
# amount at the end of the line. Not a subtotal, a "you saved" line, or an account
# balance (points, a gift card, store credit) printed after the sale.
_AMOUNT = r"(\d{1,3}(?:[,.]\d{3})+[.,]\d{2}|\d{1,6}[.,]\d{2})"
_TOTAL_LINE = re.compile(
    r"\b(?P<label>total|balance(?:\s+due)?|amount\s+due)\b[^0-9\n]*?" + _AMOUNT + r"\s*$",
    re.IGNORECASE,
)
_NOT_TOTAL = re.compile(
    r"sub\s*-?\s*total|sav(?:ed|ings?)|tax\s+total|items?\b|"
    r"points?|rewards?|gift|card\s+bal|credit|loyalty|member|remaining|available|previous|prior",
    re.IGNORECASE,
)


def _money(text: str) -> Decimal:
    """1,234.50 or 1.234,50 or 12,34: the last separator is the decimal mark."""
    whole, cents = text[:-3], text[-2:]
    return Decimal(re.sub(r"[.,]", "", whole) + "." + cents)


def printed_total_line(text: str) -> tuple[Decimal, bool] | None:
    """The receipt's labelled total and whether its label is a strong one.

    TOTAL, AMOUNT DUE and BALANCE DUE are strong; a bare BALANCE is weak, used
    only when nothing stronger is printed, because some tills print an account
    balance under that word after the sale. Among equals the last line wins: a
    total comes after the lines it sums.
    """
    best: tuple[Decimal, bool] | None = None
    for line in text.splitlines():
        if _NOT_TOTAL.search(line):
            continue
        m = _TOTAL_LINE.search(line.strip())
        if not m:
            continue
        strong = m.group("label").lower() != "balance"
        if best is None or strong or not best[1]:
            best = (_money(m.group(2)), strong)
    return best


def printed_total_from_text(text: str) -> Decimal | None:
    """The receipt's total read straight from its text.

    Some tills label the total BALANCE rather than TOTAL, and the model then
    reported no total at all, so the receipt could not be checked against its
    lines. Read from OCR text, which is untrusted: only the amount is taken, and
    only when it parses as money.
    """
    found = printed_total_line(text)
    return None if found is None else found[0]


def amount_in_text(amount: Decimal, text: str) -> bool:
    """Whether an amount is printed anywhere in the text, with either decimal mark."""
    whole, _, cents = f"{amount.quantize(Decimal('0.01'))}".partition(".")
    return re.search(rf"(?<![\d.,]){re.escape(whole)}[.,]{cents}(?!\d)", text) is not None


def header_output(
    header: ReceiptHeader | None,
    *,
    model_attempts: int,
    purchased_at: datetime | None,
    match: LocationMatch,
    flags: list[str],
) -> dict[str, Any]:
    if header is None:
        return {
            "parsed": False,
            "reason": "invalid_model_output",
            "model_attempts": model_attempts,
            "flags": flags,
            "location": match.as_json(),
        }
    return {
        "parsed": True,
        "model_attempts": model_attempts,
        "merchant_name": header.merchant_name,
        "store_identifier": header.store_identifier,
        "address_text": header.address_text,
        "phone_text": header.phone_text,
        "purchased_at_local": header.purchased_at_local,
        "purchased_at": None if purchased_at is None else purchased_at.isoformat(),
        "subtotal": None if header.subtotal is None else str(header.subtotal),
        "tax": None if header.tax is None else str(header.tax),
        "total": None if header.total is None else str(header.total),
        "flags": flags,
        "location": match.as_json(),
    }
