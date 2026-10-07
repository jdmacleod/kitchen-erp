"""Lines stage: receipt body to structured purchase lines, plus reconciliation.

Parsing is tiered: a registered vendor parser (:mod:`app.ingest.parsers`),
then the generic language-model parser constrained to :class:`ReceiptLines`.
Model output is refined deterministically: units are normalized through the
unit table, weighed ("2.31 lb @ 3.99/lb") and counted ("2 @ 1.99") patterns
fill quantities the model left empty, and discounts and deposits attach to the
item printed directly above them when the model gave no parent.

Conventions for stored lines: ``line_total`` is always the positive magnitude
printed on the line. A discount is subtracted by virtue of its kind, so the
observation price of an item is ``item.line_total - sum(attached discounts)``.

Re-running the stage on a job that already has a draft purchase deletes that
draft's lines and recreates them, and rewrites the header fields; the purchase
row and its id are kept, so ``ingest_job.purchase_id`` stays valid. A purchase
that is no longer a draft is never touched (``purchase_not_draft``).

Stage output shape::

    {"parsed": true, "parser": "llm-generic", "parser_version": "1", "model_attempts": 1,
     "purchase_id": "...", "line_count": 9,
     "lines": [{"seq": 1, "purchase_line_id": "...", "raw_text": "...",
                "line_kind": "item", "qty": "2.31", "unit": "lb", "unit_price": "3.99",
                "line_total": "9.22", "parent_seq": null, "flags": []}],
     "reconciliation": {"checked": true, "items": "..", "discounts": "..", "tax": "..",
                        "tax_source": "lines" | "header" | "none", "deposits": "..",
                        "fees": "..", "computed_total": "..", "printed_total": "..",
                        "difference": "0.00", "mismatch": false},
     "purchase_flags": ["reconcile_mismatch", ...]}

When nothing parses: ``{"parsed": false, "reason": "invalid_model_output",
"model_attempts": 3, "purchase_id": "...", "line_count": 0, ...}``; the draft
purchase exists with no lines and the flag ``lines_unparsed``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.ids import new_id
from app.ingest.errors import StageFailure
from app.ingest.schemas import ReceiptLine, ReceiptLines
from app.ingest.witness import appears_in
from app.models import IngestJob, Purchase, PurchaseLine, ReceiptDocument
from app.services.normalize import normalize_receipt_text
from app.units import UnitParseFailure, parse_unit

LINES_TASK = (
    "List every purchased line of this receipt in order. Include item lines, "
    "discounts or savings printed beneath an item, container deposits (CRV, redemption "
    "value, bottle deposit), fees (bag fee, surcharge), and the tax line(s) printed in "
    "the totals block. Exclude the store header, subtotal, total, tender, change, "
    "loyalty summaries, and footer text. Keep raw_text exactly as printed."
)

# A receipt longer than this many rows is read in parts of about this size (#60).
# gpt-oss reasons before it answers, and on a long receipt the reasoning can run
# until the context is full, leaving no answer at all: a 77-row receipt failed
# that way every time, deterministically, while its four 25-row parts all
# answered (28 lines, in less total time). A bigger context only let the
# reasoning run longer (16k filled too, after 330 s), and less reasoning
# ("think: low") returned 2 of 28 lines. Small parts keep each answer, and each
# runaway, small; a part that still fails costs only its own lines.
LINES_CHUNK_ROWS = 25
# A row that belongs to the one above it: the weight or count line of a weighed
# item, or a saving or deposit printed beneath it. A part never starts with one.
_CONTINUATION = re.compile(
    r"^\s*(?:\d+(?:\.\d+)?\s*(?:lbs?|kg|oz|g|ea)?\s*@|.*\b(?:savings?|discount|coupon|you saved|"
    r"member|crv|deposit|redemption)\b)",
    re.IGNORECASE,
)

GENERIC_PARSER = "llm-generic"
# 2: printed weights and counts override the model's, and assumed quantities are flagged (#31).
# 3: the #31 prompt wording is withdrawn and a null quantity on a plain line is not flagged,
#    after a live run against gpt-oss:20b: the model gives no quantity for any line under
#    either prompt, and the new wording made it read pack sizes ("EGGS 12") as quantities.
# 4: long receipts are read in parts of LINES_CHUNK_ROWS rows (#60).
# 5: regular-price rows are folded into the item and its saving (#64).
# 6: a weight or count printed on a row of its own joins the item it belongs to (#87).
# 7: OCR's "Ib", "1b" and "|b" read as pounds and ".65" as a rate; an amount whose tax
#    letter OCR read as a third decimal digit is cut back to its cents.
# 8: line-structure passes (app.ingest.structure, #181): a "2 QTY" prefix, an unjoined
#    weight row at zero, a product read as a discount, a regular price the model left
#    out, footer sentences, and a tax line read at its base.
GENERIC_PARSER_VERSION = "8"
RECONCILE_TOLERANCE = Decimal("0.02")
CENTS = Decimal("0.01")

# A rate as tills print it, with or without a leading zero ("@ $ .65").
_RATE = r"\$?\s*(\d+(?:\.\d+)?|\.\d+)"
# Pounds as OCR reads them too: "Ib", "1b" and "|b" for "lb". The "@ rate" that must
# follow makes the reading safe; a bare "Ib" is only ever a loose weight (below).
_POUND_OCR = re.compile(r"[il1|]bs?", re.IGNORECASE)
WEIGHT_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(lbs?|[il1|]bs?|kg|oz|g)\b\s*@\s*" + _RATE, re.IGNORECASE
)
COUNT_PATTERN = re.compile(r"(?<![\d.])(\d{1,3})\s*@\s*" + _RATE, re.IGNORECASE)
# A weight that is printed but did not survive OCR cleanly: "1.24 1b", "0.85 Ib", a
# weight with no "@ price". Enough to know the line is weighed, not enough to read it.
# Grams count only with more after them on the line: a trailing single letter is as
# likely a tax code ("2.49 G") as a unit.
LOOSE_WEIGHT_PATTERN = re.compile(
    r"(?<![\d.])\d+\.\d+\s*(?:(?:lbs?|[1i]bs?|kg|oz)\b|g\b(?=\s*\S))", re.IGNORECASE
)

# Flags that say the quantity is not what a person or the printed line said.
# qty_inferred: read from the printed pattern because the model gave none.
# qty_corrected: the printed pattern disagreed with the model, and the print won.
# qty_assumed: the line looks weighed but its weight could not be read, so whatever
#   quantity it carries is unsupported. A plain line with no quantity is one each and
#   is not flagged: nothing printed says otherwise, and flagging it flagged every line,
#   since the model gives no quantity for plain lines at all.
# qty_from_line_above, qty_from_line_below: the weight or count was printed on a
#   row of its own, above or below the item, and the rows were joined (#87).
# quantity_line: a row that is only a weight or count and could not be joined
#   to an item with certainty; review offers to merge it.
# qty_from_prefix: the count was printed before the name ("3 QTY ...", #181).
QTY_FLAGS = frozenset(
    {
        "qty_inferred",
        "qty_from_prefix",
        "qty_corrected",
        "qty_assumed",
        "qty_from_line_above",
        "qty_from_line_below",
        "quantity_line",
    }
)


@dataclass
class ParsedLine:
    seq: int
    raw_text: str
    line_kind: str
    qty: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    line_total: Decimal
    parent_seq: int | None = None
    flags: list[str] = field(default_factory=list)
    purchase_line_id: uuid.UUID | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "purchase_line_id": None
            if self.purchase_line_id is None
            else str(self.purchase_line_id),
            "raw_text": self.raw_text,
            "line_kind": self.line_kind,
            "qty": None if self.qty is None else str(self.qty),
            "unit": self.unit,
            "unit_price": None if self.unit_price is None else str(self.unit_price),
            "line_total": str(self.line_total),
            "parent_seq": self.parent_seq,
            "flags": list(self.flags),
        }


def split_receipt(receipt_text: str) -> list[str]:
    """The receipt as one part, or as parts of about LINES_CHUNK_ROWS rows.

    Blank rows are dropped. A part is extended by up to three rows rather than
    end just above a row that continues the one before it, so an item keeps its
    weight line and its saving.
    """
    rows = [row for row in receipt_text.splitlines() if row.strip()]
    if len(rows) <= LINES_CHUNK_ROWS:
        return [receipt_text]
    parts: list[str] = []
    start = 0
    while start < len(rows):
        end = min(start + LINES_CHUNK_ROWS, len(rows))
        grow = 0
        while end < len(rows) and grow < 3 and _CONTINUATION.match(rows[end]):
            end += 1
            grow += 1
        parts.append("\n".join(rows[start:end]))
        start = end
    return parts


def part_task(index: int, count: int) -> str:
    """The lines task for part ``index`` (1-based) of ``count``."""
    if count == 1:
        return LINES_TASK
    # Without the rest of the receipt around it, a footer part's savings summary
    # looked like purchased lines to the model; say that a part may have none.
    return (
        f"{LINES_TASK} This is part {index} of {count} of one receipt, split for length: "
        "list only the lines in this part. A part may hold only the store header, the "
        "totals, the payment or the footer; then return an empty list."
    )


# A row that ends in an amount and is not a total, tax or tender row: what an item
# looks like on a till receipt. Only counted, to tell a part that plainly holds
# items from one that is all header or footer.
_PRICED_ROW = re.compile(r"\d+[.,]\d{2}\s*-?\s*[A-Za-z*$]{0,2}\s*$")
# Whole words only: "card" must not exclude CARDAMOM, nor "cash" CASHMERE.
_NOT_AN_ITEM = re.compile(
    r"sub\s*total|\b(?:total|tax|visa|mastercard|master|amex|debit|credit|change|cash|"
    r"balance|amount|approved|tender(?:ed)?|auth|card|purchase|usd|bal|savings?)\b",
    re.IGNORECASE,
)
# A part with at least this many item-like rows that comes back with no items was
# not read, whatever the reply says: a valid, empty answer for a part holding a
# receipt's every item happened on a real receipt, and would otherwise pass unseen.
EMPTY_PART_MIN_ROWS = 3


def item_like_rows(part: str) -> int:
    return sum(
        1 for row in part.splitlines() if _PRICED_ROW.search(row) and not _NOT_AN_ITEM.search(row)
    )


def lines_budget_seconds(receipt_text: str) -> float:
    """How long one lines-stage request may wait for the model: the base budget
    plus a little per line of receipt text, since generation time grows with the
    lines it has to write out (#34). Capped by the stage's deadline."""
    settings = get_settings()
    lines = sum(1 for row in receipt_text.splitlines() if row.strip())
    budget = settings.llm_timeout_seconds + settings.llm_lines_seconds_per_line * lines
    return min(budget, lines_deadline_seconds())


def lines_deadline_seconds() -> float:
    """How long the whole lines stage may take, every retry included.

    Under the job's lock: a stage still working when the lock lapses could be
    claimed by a second worker and read twice.
    """
    return 0.8 * get_settings().ingest_lock_timeout_seconds


def _unit(text: str | None) -> str | None:
    if text is None or not text.strip():
        return None
    if _POUND_OCR.fullmatch(text.strip()):
        return "lb"
    parsed = parse_unit(text)
    return None if isinstance(parsed, UnitParseFailure) else parsed


def refine_line(seq: int, line: ReceiptLine) -> ParsedLine:
    """Normalize units and fill quantities from the printed patterns when absent."""
    flags: list[str] = []
    qty, unit_price = line.qty, line.unit_price
    unit = _unit(line.unit)
    if line.unit is not None and unit is None:
        flags.append("unknown_unit")
    if line.line_kind == "item":
        # What the receipt prints outranks what the model says (non-negotiable 6:
        # never guess): a printed weight or count is read from the text, and a
        # quantity nothing supports is marked as assumed for review (#31).
        weighed = WEIGHT_PATTERN.search(line.raw_text)
        counted = COUNT_PATTERN.search(line.raw_text) if weighed is None else None
        printed: tuple[Decimal, str | None, Decimal] | None = None
        if weighed is not None:
            printed = (
                Decimal(weighed.group(1)),
                _unit(weighed.group(2)),
                Decimal(weighed.group(3)),
            )
        elif counted is not None:
            printed = (Decimal(counted.group(1)), "each", Decimal(counted.group(2)))
        if printed is not None:
            p_qty, p_unit, p_price = printed
            if qty is None:
                flags.append("qty_inferred")
            elif qty != p_qty or (unit is not None and p_unit is not None and unit != p_unit):
                flags.append("qty_corrected")
            qty, unit = p_qty, p_unit or unit or "each"
            # The printed rate with the printed quantity, or the line contradicts
            # itself: three at a model's 5.00 beside a printed 3 @ 1.25.
            unit_price = p_price
        elif LOOSE_WEIGHT_PATTERN.search(line.raw_text):
            # A weight is printed but could not be read: whatever the model said,
            # or the one-each default, nothing on the line supports it.
            if qty is None:
                qty, unit = Decimal("1"), "each"
            flags.append("qty_assumed")
        elif qty is None:
            # A plain item line is one pack.
            qty, unit = Decimal("1"), "each"
        if unit is None:
            unit = "each"
    line_total = line.line_total
    fixed = _tax_code_read_as_digit(line.raw_text, line_total)
    if fixed is not None:
        line_total = fixed
        flags.append("tax_code_as_digit")
    return ParsedLine(
        seq=seq,
        raw_text=line.raw_text,
        line_kind=line.line_kind,
        qty=qty,
        unit=unit,
        unit_price=unit_price,
        line_total=line_total,
        flags=flags,
    )


def _tax_code_read_as_digit(raw_text: str, line_total: Decimal) -> Decimal | None:
    """The amount a line really prints when OCR read its tax letter as a digit.

    Tills print cents and a tax letter after them ("6.37 S"). When OCR joins the
    letter to the amount as a digit ("6.378"), the model reads a third decimal
    place and rounds it ("6.38"). An amount at the end of the line with exactly
    three decimals, which the model's reading matches to the cent, is the
    printed cents with the letter cut off: never rounded, always truncated.
    """
    match = _TRAILING_AMOUNT.search(raw_text.strip())
    token = match.group(1) if match else ""
    if not re.fullmatch(r"\d+\.\d{3}", token):
        return None
    read = Decimal(token)
    if abs(read - line_total) > CENTS:
        return None
    return Decimal(token[:-1])


def attach_parents(model_lines: list[ReceiptLine], parsed: list[ParsedLine]) -> None:
    """Set ``parent_seq`` for discounts and deposits (1-based, item lines only)."""
    for i, (raw, line) in enumerate(zip(model_lines, parsed, strict=True)):
        if line.line_kind not in ("discount", "deposit"):
            continue
        p = raw.parent_index
        if p is not None and 0 <= p < len(parsed) and parsed[p].line_kind == "item" and p != i:
            line.parent_seq = parsed[p].seq
            continue
        if p is not None:
            line.flags.append("parent_rejected")
        # Printed directly beneath an item, or beneath another attachment of one.
        j = i - 1
        while j >= 0 and parsed[j].line_kind in ("discount", "deposit"):
            if parsed[j].parent_seq is not None:
                line.parent_seq = parsed[j].parent_seq
                line.flags.append("parent_inferred")
                break
            j -= 1
        else:
            if j >= 0 and parsed[j].line_kind == "item":
                line.parent_seq = parsed[j].seq
                line.flags.append("parent_inferred")


# Flags that say a line's price is probably misread (#59). Suggestions only: a
# person confirms any change, and editing the line's total clears them.
# decimal_missing: the amount printed at the end of the line is two or more bare
#   digits ("OAT MILK 349", "CRV 30") and is what the line was read as. Tills
#   print cents, so this is almost always a decimal point OCR lost; the likely
#   price is a hundredth of it. On the real receipts behind #59 the two-digit
#   case caught a container deposit and an item, both read at 100 times their price,
#   and nothing else.
#   A single digit is left alone: its hundredth is rarely a price.
# exceeds_total: one line costs more than the whole printed receipt.
# tax_code_as_digit: the amount read as "6.378" was the printed 6.37 and its tax
#   letter; it was cut back to the cents (see _tax_code_read_as_digit).
# no_amount_printed, regular_price_from_text, tax_from_rate: a line-structure pass
#   set the amount from the printed arithmetic (app.ingest.structure, #181).
# not_in_scan: the amount is printed nowhere in the OCR text (#121). OCR drops
#   prices, and the reader fills the gap with a number from elsewhere.
PRICE_FLAGS = frozenset(
    {
        "decimal_missing",
        "exceeds_total",
        "tax_code_as_digit",
        "no_amount_printed",
        "regular_price_from_text",
        "tax_from_rate",
        "not_in_scan",
        "points_not_money",
        "payment_row",
        "continuation_row",
    }
)

# The last amount on a line, and whatever tax or flag letters follow it.
_TRAILING_AMOUNT = re.compile(r"(\d[\d.,]*)\s*(?:[A-Za-z*]{1,2}\s*)?$")


def restored(amount: Decimal) -> Decimal:
    """What a decimal_missing amount most likely was: its hundredth."""
    return (amount / 100).quantize(CENTS)


def check_prices(lines: list[ParsedLine], printed_total: Decimal | None) -> None:
    """Flag lines whose price looks misread. Never changes a price."""
    # What was paid for a line: its total less the discounts attached to it. A
    # regular price folded onto an item with its saving can exceed the receipt's
    # total on its own while the price paid does not.
    discounts: dict[int, Decimal] = {}
    for line in lines:
        if line.line_kind == "discount" and line.parent_seq is not None:
            discounts[line.parent_seq] = (
                discounts.get(line.parent_seq, Decimal("0")) + line.line_total
            )
    for line in lines:
        if line.line_kind == "discount":
            continue
        match = _TRAILING_AMOUNT.search(line.raw_text.strip())
        token = match.group(1) if match else ""
        if len(token) >= 2 and token.isdigit() and Decimal(token) == line.line_total:
            line.flags.append("decimal_missing")
        paid = line.line_total - discounts.get(line.seq, Decimal("0"))
        if printed_total is not None and paid > printed_total + RECONCILE_TOLERANCE:
            line.flags.append("exceeds_total")


def flag_amounts_not_in_scan(lines: list[ParsedLine], ocr_text: str) -> None:
    """Flag every line whose amount the OCR text never prints (#121, ruling R3).

    Matched on digits alone (``witness.appears_in``), so a lost decimal point or a
    decimal comma is not a miss. The amount is never changed: the flag says where
    to look. With no OCR text there is nothing to compare against, so nothing is
    flagged rather than everything.
    """
    if not ocr_text.strip():
        return
    for line in lines:
        if not appears_in(line.line_total, ocr_text) and "not_in_scan" not in line.flags:
            line.flags.append("not_in_scan")


def restoring_decimals_reconciles(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None
) -> bool:
    """True when the lines miss the printed total but match it once every
    decimal_missing amount is read as its hundredth."""
    suspects = [line for line in lines if "decimal_missing" in line.flags]
    if printed_total is None or not suspects:
        return False
    if not reconcile(lines, printed_total, header_tax)["mismatch"]:
        return False
    fixed = [
        replace(line, line_total=restored(line.line_total))
        if "decimal_missing" in line.flags
        else line
        for line in lines
    ]
    return not reconcile(fixed, printed_total, header_tax)["mismatch"]


# A shelf-price row: "Regular Price 3.29", OCR's "Resular", "Reqular" and
# "Reaular Price", "Reg. Price", "Original Price". It says what the item would
# have cost; it is never a purchased line (#64).
_REGULAR_PRICE = re.compile(
    r"^\W*(?:re[gsqa]ular|reg\.?|orig(?:inal)?\.?)\s+pr[il1]ce\b", re.IGNORECASE
)
# A saving printed beneath it: "Card Savings 0.30-", or "You saved 0.30" on the
# same row as the regular price.
# OCR spells it "Savinss" and "Yau saved" too; only ever read beside a
# regular-price row, so a looser match cannot catch an item.
_SAVING = re.compile(r"sav\w*|discount", re.IGNORECASE)
_AMOUNT = re.compile(r"(\d+)[.,](\d{2})")


def _amounts(text: str) -> list[Decimal]:
    return [Decimal(f"{whole}.{cents}") for whole, cents in _AMOUNT.findall(text)]


def _looks_like_a_saving(line: ParsedLine) -> bool:
    """A saving, not an item whose name happens to start "sav" (SAVORY CRACKERS):
    the model read it as a discount, or its amount is printed negative."""
    return line.line_kind == "discount" or bool(re.search(r"\d-\s*\S{0,2}\s*$", line.raw_text))


def fold_regular_prices(lines: list[ParsedLine]) -> tuple[list[ParsedLine], list[str]]:
    """Normalize loyalty-card layouts; return the kept lines and the dropped rows.

    Such receipts print an item at what was paid, then its regular price, then
    the saving (#64)::

        PORK SHOULDER ROAST      14.56 S
        Regular Price            17.00
        Card Savings              2.44-

    Read row by row, the regular price became another item and the saving a
    discount taken off an amount that was already net. The regular-price row is
    dropped: it is never a purchased line. When the printed numbers agree (the
    regular price less the saving is what the item line says), the item takes
    the regular price and the saving attaches to it as a discount, which is how
    every other receipt's savings are stored: the observation price is still
    the net amount, now marked as a promotion, and the lines reconcile. When
    they do not agree (OCR misread one of the three, or the regular price is
    per unit of a multi-buy), the regular-price row and its saving are both
    dropped: the item line is already what was paid, so subtracting the saving
    from it would count it twice. The money stays right; only the promotion
    marker is lost.
    """
    dropped: list[str] = []
    kept: list[ParsedLine] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _REGULAR_PRICE.search(line.raw_text):
            kept.append(line)
            i += 1
            continue
        dropped.append(line.raw_text)
        item = next((k for k in reversed(kept) if k.line_kind == "item"), None)
        regular = _amounts(line.raw_text)
        # The saving: on this row after the price, or the next row.
        saving_line: ParsedLine | None = None
        saving: Decimal | None = None
        if _SAVING.search(line.raw_text) and len(regular) >= 2:
            saving = regular[1]
        elif (
            i + 1 < len(lines)
            and _SAVING.search(lines[i + 1].raw_text)
            and _looks_like_a_saving(lines[i + 1])
        ):
            saving_line = lines[i + 1]
            found = _amounts(saving_line.raw_text)
            saving = found[-1] if found else None
        if (
            item is not None
            and kept
            and kept[-1] is item
            and regular
            and saving is not None
            and abs(regular[0] - saving - item.line_total) <= RECONCILE_TOLERANCE
        ):
            item.line_total = regular[0]
            if (
                item.unit_price is not None
                and item.qty is not None
                and abs(item.unit_price * item.qty - item.line_total) > RECONCILE_TOLERANCE
            ):
                item.unit_price = None  # the printed rate was the sale rate
            discount = saving_line or replace(line, flags=[])
            discount.line_kind = "discount"
            discount.line_total = saving
            discount.parent_seq = item.seq
            discount.qty = discount.unit = discount.unit_price = None
            if saving_line is None:
                dropped.pop()  # the one-row form is kept, as the discount
            kept.append(discount)
            i += 2 if saving_line is not None else 1
            continue
        if saving_line is not None:
            dropped.append(saving_line.raw_text)
            i += 2
            continue
        i += 1
    return _renumber(kept), dropped


def _renumber(lines: list[ParsedLine]) -> list[ParsedLine]:
    """Sequence numbers 1..n again, with parents following their items."""
    new_seq = {line.seq: n for n, line in enumerate(lines, start=1)}
    for n, line in enumerate(lines, start=1):
        line.parent_seq = new_seq.get(line.parent_seq) if line.parent_seq is not None else None
        line.seq = n
    return lines


@dataclass(frozen=True)
class PrintedQuantity:
    """A row that is only a weight or count, as printed."""

    qty: Decimal
    unit: str
    rate: Decimal
    amount: Decimal | None  # what it comes to, when the row prints that too

    def comes_to(self, total: Decimal) -> bool:
        """The quantity at the rate is ``total``, to within a cent of rounding."""
        return abs(self.qty * self.rate - total) <= CENTS


# "/lb" after a rate, and what may be left once the quantity and amounts are out
# of a quantity row: nothing, or a tax letter or two.
_RATE_UNIT = re.compile(r"/\s*(?:lbs?|[il1|]bs?|kg|oz|g|ea|each)\b", re.IGNORECASE)
_TAX_CODE = re.compile(r"[A-Za-z*]{0,2}")


def quantity_only(raw_text: str) -> PrintedQuantity | None:
    """The row's weight or count when that is all the row holds, else None.

    "2.71 lb @ 3.99 /lb" and "5 @ 0.79" qualify, as does "2.31 lb @ 0.69/lb 1.59 F"
    with its amount and tax letter. A row with a name ("AVOCADO 3 @ 0.69 2.07"), a
    negative amount or two amounts does not.
    """
    weighed = WEIGHT_PATTERN.search(raw_text)
    match = weighed or COUNT_PATTERN.search(raw_text)
    if match is None:
        return None
    rest = _RATE_UNIT.sub(" ", raw_text[: match.start()] + " " + raw_text[match.end() :])
    amounts = _amounts(rest)
    rest = _AMOUNT.sub(" ", rest).replace("$", " ")
    if len(amounts) > 1 or not _TAX_CODE.fullmatch("".join(rest.split())):
        return None
    if weighed is not None:
        qty, unit, rate = Decimal(match.group(1)), _unit(match.group(2)), match.group(3)
    else:
        qty, unit, rate = Decimal(match.group(1)), "each", match.group(2)
    if unit is None:
        return None
    return PrintedQuantity(qty, unit, Decimal(rate), amounts[0] if amounts else None)


def prints_an_amount(raw_text: str) -> bool:
    """Whether the row prints an amount (a name row such as "BANANAS" does not)."""
    return _AMOUNT.search(raw_text) is not None


def _prints_its_quantity(line: ParsedLine) -> bool:
    return bool(WEIGHT_PATTERN.search(line.raw_text) or COUNT_PATTERN.search(line.raw_text))


def merge_quantity_lines(lines: list[ParsedLine]) -> tuple[list[ParsedLine], list[str]]:
    """Join each weight or count printed on a row of its own to its item (#87).

    Tills print a weighed or counted item on two rows, and the model reads each
    row as a line of its own, so the item is counted twice. The quantity can
    come first, or the name can::

        2.71 lb @ 3.99 /lb
        WT BROCCOLI CROWNS    10.81 F

        BANANAS
          2.31 lb @ 0.69/lb    1.59

    Nothing is joined on the model's word. A quantity row with no amount joins
    the item above or below it whose printed amount is the quantity at the rate;
    when both would, or neither, it is left alone and flagged ``quantity_line``
    for review. A quantity row that prints its own amount, which must be the
    quantity at the rate, joins a name row above it that prints no amount, and
    that item takes the amount. The model's own reading of a quantity row is
    never used: it gets their amounts wrong, and reads a count row as a discount.
    Returns the kept lines and the quantity rows that were joined.
    """
    merged: list[str] = []
    joined: dict[int, int] = {}  # seq of a quantity row -> seq of the item it joined
    taken: set[int] = set()  # seqs of items that already took a quantity row

    def item_at(j: int) -> ParsedLine | None:
        if not 0 <= j < len(lines):
            return None
        other = lines[j]
        if (
            other.line_kind != "item"
            or other.seq in joined
            or other.seq in taken
            or _prints_its_quantity(other)
        ):
            return None
        return other

    for i, line in enumerate(lines):
        if line.line_kind not in ("item", "discount") or line.seq in taken:
            continue
        printed = quantity_only(line.raw_text)
        if printed is None:
            continue
        target: ParsedLine | None = None
        flag = ""
        above, below = item_at(i - 1), item_at(i + 1)
        if printed.amount is None:
            # The amount is on the item's row; it has to be printed there, since
            # a total the model gave a row with no amount proves nothing.
            fits = [
                (other, flag)
                for other, flag in ((above, "qty_from_line_below"), (below, "qty_from_line_above"))
                if other is not None
                and prints_an_amount(other.raw_text)
                and printed.comes_to(other.line_total)
            ]
            if len(fits) == 1:
                target, flag = fits[0]
        elif (
            above is not None
            and not prints_an_amount(above.raw_text)
            and printed.comes_to(printed.amount)
        ):
            target, flag = above, "qty_from_line_below"
            target.line_total = printed.amount
        if target is None:
            if "quantity_line" not in line.flags:
                line.flags.append("quantity_line")
            continue
        target.qty, target.unit, target.unit_price = printed.qty, printed.unit, printed.rate
        target.flags = [f for f in target.flags if f not in QTY_FLAGS] + [flag]
        joined[line.seq] = target.seq
        taken.add(target.seq)
        merged.append(line.raw_text)
    kept = [line for line in lines if line.seq not in joined]
    for line in kept:
        if line.parent_seq in joined:
            line.parent_seq = joined[line.parent_seq]
    return _renumber(kept), merged


def parse_model_lines(result: ReceiptLines) -> list[ParsedLine]:
    parsed = [refine_line(i + 1, line) for i, line in enumerate(result.lines)]
    attach_parents(result.lines, parsed)
    return parsed


def reconcile(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None
) -> dict[str, Any]:
    sums = {k: Decimal("0") for k in ("item", "discount", "tax", "deposit", "fee")}
    for line in lines:
        sums[line.line_kind] += line.line_total
    tax_source = "lines"
    if not any(line.line_kind == "tax" for line in lines):
        if header_tax is not None:
            sums["tax"], tax_source = header_tax, "header"
        else:
            tax_source = "none"
    computed = sums["item"] - sums["discount"] + sums["tax"] + sums["deposit"] + sums["fee"]
    out: dict[str, Any] = {
        "checked": printed_total is not None,
        "items": str(sums["item"]),
        "discounts": str(sums["discount"]),
        "tax": str(sums["tax"]),
        "tax_source": tax_source,
        "deposits": str(sums["deposit"]),
        "fees": str(sums["fee"]),
        "computed_total": str(computed.quantize(CENTS)),
        "printed_total": None if printed_total is None else str(printed_total),
        "difference": None,
        "mismatch": False,
    }
    if printed_total is not None:
        difference = (computed - printed_total).quantize(CENTS)
        out["difference"] = str(difference)
        out["mismatch"] = abs(difference) > RECONCILE_TOLERANCE
    return out


def computed_total(lines: list[ParsedLine]) -> Decimal:
    total = Decimal("0")
    for line in lines:
        total += -line.line_total if line.line_kind == "discount" else line.line_total
    return total.quantize(CENTS)


async def upsert_draft_purchase(
    db: AsyncSession,
    job: IngestJob,
    document: ReceiptDocument,
    *,
    purchased_at: datetime,
    subtotal: Decimal | None,
    tax: Decimal | None,
    total: Decimal,
    vendor_location_id: uuid.UUID | None,
    flags: list[str],
    lines: list[ParsedLine],
) -> Purchase:
    """Create the receipt's draft purchase, or rewrite the one the job already has."""
    purchase: Purchase | None = None
    if job.purchase_id is not None:
        purchase = await db.get(Purchase, job.purchase_id)
    if purchase is not None:
        if purchase.status != "draft":
            raise StageFailure(code="purchase_not_draft")
        await db.execute(delete(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id))
        purchase.purchased_at = purchased_at
        purchase.subtotal = subtotal
        purchase.tax = tax
        purchase.total = total
        purchase.vendor_location_id = vendor_location_id
        purchase.flags = list(flags)
    else:
        purchase = Purchase(
            id=new_id(),
            vendor_location_id=vendor_location_id,
            receipt_document_id=document.id,
            purchased_at=purchased_at,
            subtotal=subtotal,
            tax=tax,
            total=total,
            status="draft",
            source="receipt",
            entered_by=document.uploaded_by,
            flags=list(flags),
        )
        db.add(purchase)
        await db.flush()  # the purchase row must exist before the job points at it
        job.purchase_id = purchase.id
    await db.flush()

    by_seq: dict[int, uuid.UUID] = {}
    for line in lines:
        line.purchase_line_id = new_id()
        by_seq[line.seq] = line.purchase_line_id
    for line in lines:
        db.add(
            PurchaseLine(
                id=line.purchase_line_id,
                purchase_id=purchase.id,
                seq=line.seq,
                raw_text=line.raw_text,
                raw_text_norm=normalize_receipt_text(line.raw_text),
                line_kind=line.line_kind,
                parent_line_id=None if line.parent_seq is None else by_seq.get(line.parent_seq),
                qty=line.qty,
                unit=line.unit,
                unit_price=line.unit_price,
                line_total=line.line_total,
                resolution="unmatched",
                flags=list(line.flags),
            )
        )
    await db.flush()
    return purchase
