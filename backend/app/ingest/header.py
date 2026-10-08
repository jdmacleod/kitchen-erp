"""Header stage: vendor, location, purchase time, and totals.

Field extraction is the language model constrained to :class:`ReceiptHeader`.
Location matching combines five kinds of evidence, each computed with bound
parameters (merchant text never reaches SQL as a fragment):

* an exact match between a location's ``receipt_identifiers`` and the store
  identifier the model read, or a token in the OCR text (weight 1.0);
* proximity of the document's ``capture_geo`` to a location, within
  ``INGEST_LOCATION_RADIUS_M`` (weight 0.7);
* trigram similarity between the printed merchant name and vendor names, at
  0.3 or better (weight 0.6 × similarity);
* the printed phone, compared by its digits with each location's phone, counted
  only when it is the number of exactly one branch of its vendor (weight 1.0;
  a number several branches share is recorded as ``phone_shared`` and adds
  nothing);
* trigram similarity between the printed address and each location's address, at
  0.4 or better (weight 0.6 × similarity).

With no phone or address on either side, the last two add nothing and ranking
is exactly what it was before them (eng review R8).

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
                                               "name_similarity": "1.000" | null,
                                               "phone": "555-0142" | null,
                                               "phone_shared": false,
                                               "address_similarity": "0.812" | null}}]}}

When the model's reply never validates: ``{"parsed": false, "reason":
"invalid_model_output", "model_attempts": 3, "location": {...}}``; matching still
runs on the OCR text and the job continues to ``lines`` with fallbacks.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.ingest.schemas import ReceiptHeader
from app.models import ReceiptDocument
from app.models.geo import Place, Vendor, VendorLocation
from app.services import phone as phones

HEADER_TASK = (
    "Extract the receipt header: merchant name, store identifier (store number or "
    "code), address, phone, purchase date and time, subtotal, tax and total. Use only "
    "what is printed."
)

IDENTIFIER_WEIGHT = Decimal("1.0")
# A printed phone that is one branch's own number is as decisive as its store code.
PHONE_WEIGHT = IDENTIFIER_WEIGHT
ADDRESS_WEIGHT = Decimal("0.6")
# Addresses share words ("Blvd", a city), so a weak likeness says nothing.
ADDRESS_FLOOR = Decimal("0.4")
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


_PRINTED_DATE = re.compile(r"(?<![\d/.-])(\d{1,4})([/.-])(\d{1,2})\2(\d{2,4})(?![\d/.-])")
# The meridiem as printed, or as OCR garbles it: "pn", "prn" and "arn" for pm and am.
_PRINTED_TIME = re.compile(
    r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\s*([AaPp])\.?\s*(?:[Mm]|[Rr]?[Nn])\b\.?"
    r"|(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?![\d:])"
)
_HAS_TIME = re.compile(r"\d{1,2}:\d{2}")


def _printed_time(row: str) -> time | None:
    m = _PRINTED_TIME.search(row)
    if m is None:
        return None
    if m.group(1) is not None:
        hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if m.group(4).lower() == "p" else 0)
    else:
        hour, minute, second = int(m.group(5)), int(m.group(6)), int(m.group(7) or 0)
    return time(hour, minute, second)


def printed_dates(text: str, order: str = "MDY") -> list[tuple[date, time | None]]:
    """Every date printed in the text, read in ``order``, with a time on its row."""
    found: list[tuple[date, time | None]] = []
    for row in text.splitlines():
        for m in _PRINTED_DATE.finditer(row):
            token = m.group(0)
            for fmt in _DATE_ORDERS[order]:
                try:
                    day = datetime.strptime(token, fmt.format(s=m.group(2))).date()
                except ValueError:
                    continue
                rest = row[: m.start()] + " " + row[m.end() :]
                found.append((day, _printed_time(rest)))
                break
    return found


# How far before its upload a receipt's date may plausibly fall.
_PLAUSIBLE_AGE = timedelta(days=400)


def _year_from_reference(
    dates: dict[date, time | None], reference: date
) -> tuple[date, time | None] | None:
    """One date from several that differ only in a year OCR misread.

    The dates must share month and day, and any times printed with them must
    agree on hour and minute (am/pm may be lost or garbled on one copy). When
    exactly one year is plausible against the reference (on or before it, and
    not over about thirteen months older) that one is taken; when none is, the
    month and day on or before the reference. Otherwise None.
    """
    days = {(day.month, day.day) for day in dates}
    times = {at for at in dates.values() if at is not None}
    if len(days) != 1 or len({(at.hour % 12, at.minute, at.second) for at in times}) > 1:
        return None
    # Times that differ only by twelve hours had am/pm lost on one copy: the
    # afternoon reading is the one a meridiem printed.
    at = max(times) if times else None
    plausible = [day for day in dates if reference - _PLAUSIBLE_AGE <= day <= reference]
    if len(plausible) == 1:
        return plausible[0], at
    if plausible:
        return None
    ((month, day_of_month),) = days
    for year in (reference.year, reference.year - 1):
        try:
            candidate = date(year, month, day_of_month)
        except ValueError:  # 29 February in a year without one
            continue
        if candidate <= reference:
            return candidate, at
    return None


def datetime_from_text(
    model_value: str | None,
    model_at: datetime | None,
    text: str,
    timezone: str,
    order: str = "MDY",
    reference: datetime | None = None,
) -> tuple[datetime | None, list[str]]:
    """The purchase time, with what the receipt prints winning over the model.

    Only when the receipt prints exactly one date: the model's date gives way to
    it (``date_from_text``), and a time printed on its row fills a time the
    model left out (``time_from_text``). Several dates (a return-by date, an
    expiry) are left to the model, except one date printed twice with years OCR
    misread differently: with a ``reference`` (when the receipt was captured or
    first uploaded), the year is the one the reference makes plausible
    (``year_from_upload``).
    """
    dates: dict[date, time | None] = {}
    for day, printed in printed_dates(text, order):
        if dates.get(day) is None:
            dates[day] = printed
    zone = ZoneInfo(timezone)
    flags: list[str] = []
    if len(dates) > 1 and reference is not None:
        picked = _year_from_reference(dates, reference.astimezone(zone).date())
        if picked is not None:
            dates = {picked[0]: picked[1]}
            flags.append("year_from_upload")
    if len(dates) != 1:
        return model_at, []
    ((day, printed_at),) = dates.items()
    local = None if model_at is None else model_at.astimezone(zone)
    at = None
    if local is not None and model_value and _HAS_TIME.search(model_value):
        at = local.time()
    elif printed_at is not None:
        at = printed_at
        flags.append("time_from_text")
    if local is None or local.date() != day:
        flags.append("date_from_text")
    if not flags:
        return model_at, []
    moment = datetime.combine(day, at or time(0, 0)).replace(tzinfo=zone)
    return moment.astimezone(UTC), flags


@dataclass
class Candidate:
    vendor_location_id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str
    location_name: str
    identifier: str | None = None
    distance_m: Decimal | None = None
    name_similarity: Decimal | None = None
    # 1F (#86): the location's own number was printed; or it was, but several
    # branches share it, so it says nothing about which (eng review R9).
    phone: str | None = None
    phone_shared: bool = False
    address_similarity: Decimal | None = None

    @property
    def score(self) -> Decimal:
        score = Decimal("0")
        if self.identifier is not None:
            score += IDENTIFIER_WEIGHT
        if self.distance_m is not None:
            score += PROXIMITY_WEIGHT
        if self.name_similarity is not None:
            score += SIMILARITY_WEIGHT * self.name_similarity
        if self.phone is not None:
            score += PHONE_WEIGHT
        if self.address_similarity is not None:
            score += ADDRESS_WEIGHT * self.address_similarity
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
                "phone": self.phone,
                "phone_shared": self.phone_shared,
                "address_similarity": (
                    None if self.address_similarity is None else str(self.address_similarity)
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
    phone_text: str | None = None,
    address_text: str | None = None,
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

    # 4. The printed phone, by its digits. It counts only when it is one branch's
    #    own number: a head-office number printed by every branch, or listed on
    #    several, cannot tell them apart and adds nothing (R9).
    printed = phones.digits(phone_text or "")
    if len(printed) >= 7:
        by_vendor: dict[uuid.UUID, list[VendorLocation]] = {}
        with_phones = await db.execute(active.where(VendorLocation.phone.is_not(None)))
        for location in with_phones.scalars().unique():
            if phones.same_number(printed, phones.digits(location.phone or "")):
                by_vendor.setdefault(location.vendor_id, []).append(location)
        for matched in by_vendor.values():
            if len(matched) == 1:
                candidate(matched[0]).phone = matched[0].phone
            else:
                for location in matched:
                    if location.id in candidates:
                        candidates[location.id].phone_shared = True

    # 5. Trigram likeness of the printed address to each location's address.
    address = " ".join((address_text or "").split()).lower()
    if address:
        likeness = cast(func.similarity(func.lower(VendorLocation.address), address), Numeric)
        similar_addresses = await db.execute(
            active.add_columns(func.round(likeness, 3)).where(
                VendorLocation.address.is_not(None), likeness >= ADDRESS_FLOOR
            )
        )
        for location, sim in similar_addresses.unique():
            candidate(location).address_similarity = Decimal(str(sim))

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


# A savings summary printed after the sale ("YOUR SAVINGS" over its rows and its own
# "Total", #121): its heading names savings and prints no amount. Its total is what
# was saved, never what was paid.
_SAVINGS_HEADING = re.compile(r"sav(?:ed|ings?)|discounts?|coupons?", re.IGNORECASE)
_ENDS_A_BLOCK = re.compile(
    r"sub\s*-?\s*total|\btax\b|\btotal\b|\bbalance\b|amount\s+due", re.IGNORECASE
)
_SAVINGS_BLOCK_ROWS = 6


def _in_savings_block(rows: list[str], index: int) -> bool:
    """Whether the total on ``rows[index]`` closes a savings summary.

    Walking up from it through rows that print amounts, a heading that names
    savings and prints none comes before any subtotal, tax or other total.
    """
    for row in reversed(rows[max(index - _SAVINGS_BLOCK_ROWS, 0) : index]):
        if not re.search(_AMOUNT, row):
            return bool(_SAVINGS_HEADING.search(row))
        if _ENDS_A_BLOCK.search(row):
            return False
    return False


def savings_block_totals(text: str) -> set[Decimal]:
    """The amounts of total rows that close a savings summary."""
    rows = [row for row in text.splitlines() if row.strip()]
    found: set[Decimal] = set()
    for i, row in enumerate(rows):
        m = _TOTAL_LINE.search(row.strip())
        if m and _in_savings_block(rows, i):
            found.add(_money(m.group(2)))
    return found


def printed_total_line(text: str) -> tuple[Decimal, bool] | None:
    """The receipt's labelled total and whether its label is a strong one.

    TOTAL, AMOUNT DUE and BALANCE DUE are strong; a bare BALANCE is weak, used
    only when nothing stronger is printed, because some tills print an account
    balance under that word after the sale. Among equals the last line wins: a
    total comes after the lines it sums. The total of a savings summary is never
    the receipt's (#121).
    """
    rows = [row for row in text.splitlines() if row.strip()]
    best: tuple[Decimal, bool] | None = None
    for i, line in enumerate(rows):
        if _NOT_TOTAL.search(line):
            continue
        m = _TOTAL_LINE.search(line.strip())
        if not m or _in_savings_block(rows, i):
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


def _amount_pattern(amount: Decimal) -> re.Pattern[str]:
    whole, _, cents = f"{amount.quantize(Decimal('0.01'))}".partition(".")
    return re.compile(rf"(?<![\d.,]){re.escape(whole)}[.,]{cents}(?!\d)")


def amount_in_text(amount: Decimal, text: str) -> bool:
    """Whether an amount is printed anywhere in the text, with either decimal mark."""
    return _amount_pattern(amount).search(text) is not None


def rows_printing(amount: Decimal, text: str) -> list[str]:
    """The text's rows that print ``amount``."""
    pattern = _amount_pattern(amount)
    return [row for row in text.splitlines() if pattern.search(row)]


# The card or cash slip printed under the sale: what was tendered, not what the sale
# came to, though it repeats that amount. "TOTAL AMOUNT" there is the slip's own label.
_PAYMENT_ROW = re.compile(
    r"amount|visa|master\s*card|amex|discover|debit|credit|cash|tender|change|"
    r"approv|auth|account|card",
    re.IGNORECASE,
)


def only_on_payment_rows(amount: Decimal, text: str) -> bool:
    """Whether ``amount`` is printed, and only on rows of the payment slip."""
    rows = rows_printing(amount, text)
    return bool(rows) and all(_PAYMENT_ROW.search(row) for row in rows)


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
