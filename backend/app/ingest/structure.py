"""Deterministic passes over read lines that fix how a receipt's rows were split (#181).

The model reads a receipt row by row, and a till's layout does not always map
one row to one purchased line: a quantity is printed before the name, a weight
on a row of its own, a regular price and a saving beneath what was paid. Each
pass here fixes one such pattern when the printed numbers prove the fix, and
marks the lines it changed with a flag a reviewer can see. A pass that cannot
prove its fix leaves the lines as read.

:func:`post_passes` runs every pass, in the order the stage runs them; the
reading benchmark calls it too, so its scores are the pipeline's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.ingest.lines import (
    _REGULAR_PRICE,
    _SAVING,
    CENTS,
    RECONCILE_TOLERANCE,
    ParsedLine,
    _amounts,
    _renumber,
    fold_regular_prices,
    merge_quantity_lines,
    parse_model_lines,
    prints_an_amount,
    quantity_only,
    reconcile,
)
from app.ingest.schemas import ReceiptLines

# Flags set by the passes below. Each names what was changed and why.
# qty_from_prefix: "2 QTY NAME" printed its count before the name (pattern 1).
# no_amount_printed: a weight or count row that prints no amount of its own, which
#   could not be joined to its item, counts for nothing rather than for its rate
#   (pattern 2).
# kind_from_wording: a line read as a discount names a product with its size and
#   no saving, and the receipt adds up better as an item (pattern 3).
# regular_price_from_text: the item was read at what was paid and its saving
#   subtracted again; the regular price printed above the saving was restored
#   (pattern 4).
# footer_text: an item at zero that prints no amount and reads as a footer or
#   loyalty sentence; kept for review, never dropped (pattern 6).
# tax_from_rate: the tax line was read at its taxable base; it takes the amount
#   the base at the printed rate comes to (pattern 7).
STRUCTURE_FLAGS = frozenset(
    {
        "qty_from_prefix",
        "no_amount_printed",
        "kind_from_wording",
        "regular_price_from_text",
        "footer_text",
        "tax_from_rate",
    }
)


@dataclass
class PostPasses:
    """The lines after every pass, and the rows taken out on the way."""

    lines: list[ParsedLine]
    merged_rows: list[str] = field(default_factory=list)  # weight/count rows joined to items
    dropped_rows: list[str] = field(default_factory=list)  # shelf-price rows (#64)


def post_passes(
    result: ReceiptLines,
    receipt_text: str = "",
    printed_total: Decimal | None = None,
    header_tax: Decimal | None = None,
) -> PostPasses:
    """Every deterministic pass over the model's lines, in the stage's order."""
    parsed = parse_model_lines(result)
    parsed, merged = merge_quantity_lines(parsed)
    parsed, dropped = fold_regular_prices(parsed)
    quantity_prefix(parsed)
    regular_price_from_text(parsed, receipt_text)
    flag_footer_rows(parsed)
    unjoined_quantity_rows(parsed)
    tax_from_rate(parsed)
    items_read_as_discounts(parsed, printed_total, header_tax)
    return PostPasses(_renumber(parsed), merged, dropped)


# --- 1. A count printed before the name -------------------------------------

# "2 QTY HOUSE PASTA", "QTY 2 HOUSE PASTA". Only QTY: "2 X 12OZ" is a pack size.
_QTY_PREFIX = re.compile(
    r"^\W*(?:(?P<a>\d{1,2})\s*QTY|QTY\s*(?P<b>\d{1,2}))\s+(?=[A-Za-z])", re.IGNORECASE
)


def quantity_prefix(lines: list[ParsedLine]) -> None:
    """An item whose row starts with its count takes that count.

    The money is not changed: a till prints the extended amount at the right.
    When the row prints two amounts and the count times the first is the
    second, they are the unit price and the line total, whichever the model
    took. With one amount, the unit price is that amount over the count when
    it divides to the cent, and unknown otherwise.
    """
    for line in lines:
        if line.line_kind != "item":
            continue
        m = _QTY_PREFIX.match(line.raw_text)
        if m is None:
            continue
        count = Decimal(m.group("a") or m.group("b"))
        if count < 2 or (line.qty == count and line.unit in (None, "each")):
            continue
        amounts = _amounts(line.raw_text[m.end() :])
        if len(amounts) == 2 and abs(amounts[0] * count - amounts[1]) <= CENTS:
            line.unit_price, line.line_total = amounts[0], amounts[1]
        else:
            each = line.line_total / count
            line.unit_price = each if each == each.quantize(CENTS) else None
        line.qty, line.unit = count, "each"
        line.flags = [f for f in line.flags if f != "qty_assumed"] + ["qty_from_prefix"]


# --- 2. A weight or count row that could not be joined ----------------------


def unjoined_quantity_rows(lines: list[ParsedLine]) -> None:
    """A weight or count row printing no amount of its own counts for nothing.

    merge_quantity_lines joins such a row to its item when the arithmetic says
    which item; when it cannot, the row stays for review, flagged
    ``quantity_line``. Its amount is then whatever the model made of it, usually
    the rate or the weight, and read as a discount it was subtracted. The row
    prints no amount, so it is kept as an item at zero, and review's "Merge
    into the next line" still works on it.
    """
    for line in lines:
        if "quantity_line" not in line.flags:
            continue
        printed = quantity_only(line.raw_text)
        if printed is None or printed.amount is not None:
            continue
        line.line_kind = "item"
        line.line_total = Decimal("0")
        line.parent_seq = None
        line.qty, line.unit, line.unit_price = printed.qty, printed.unit, printed.rate
        line.flags.append("no_amount_printed")
    for line in lines:
        # Nothing hangs off a row that is not a purchase of its own.
        parent = next((p for p in lines if p.seq == line.parent_seq), None)
        if parent is not None and "no_amount_printed" in parent.flags:
            line.parent_seq = None


# --- 3. An item read as a discount ------------------------------------------

# Wording a saving uses. A line read as a discount without any of it, and with a
# pack size, names a product.
_SAVING_WORDS = re.compile(
    r"sav|disc|coupon|cpn|\boff\b|promo|member|card|reward|deal|instant|bonus|"
    r"mark\s*down|mkdn|\bsale\b|price\s*cut|%",
    re.IGNORECASE,
)
_PACK_SIZE = re.compile(
    r"(?<![\d.])\d+(?:\.\d+)?\s*(?:lbs?|oz|fl\s*oz|kg|g|ml|l|ct|pk|pack)\b", re.IGNORECASE
)
_NEGATIVE = re.compile(r"(?:\d-|-\s*\$?\d[\d.,]*)\s*[A-Za-z*]{0,2}\s*$")


def _difference(
    lines: list[ParsedLine], printed_total: Decimal, header_tax: Decimal | None
) -> Decimal:
    return abs(Decimal(reconcile(lines, printed_total, header_tax)["difference"]))


def items_read_as_discounts(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None = None
) -> None:
    """A "discount" naming a product with its size becomes an item.

    Only with the printed total to check against: the receipt must add up more
    closely with the line as an item than as a discount. A line printed
    negative, or worded as a saving, stays a discount.
    """
    if printed_total is None:
        return
    for line in lines:
        if line.line_kind != "discount" or line.line_total <= 0:
            continue
        text = line.raw_text
        if _SAVING_WORDS.search(text) or _NEGATIVE.search(text) or not _PACK_SIZE.search(text):
            continue
        if quantity_only(text) is not None:
            continue
        before = _difference(lines, printed_total, header_tax)
        line.line_kind = "item"
        if _difference(lines, printed_total, header_tax) < before:
            line.parent_seq = None
            line.flags = [f for f in line.flags if f not in ("parent_inferred", "parent_rejected")]
            line.flags.append("kind_from_wording")
            if line.qty is None:
                line.qty, line.unit = Decimal("1"), "each"
        else:
            line.line_kind = "discount"


# --- 4. A net price with its saving subtracted again ------------------------


def _rows(text: str) -> list[str]:
    return [row for row in text.splitlines() if row.strip()]


def _same_row(a: str, b: str) -> bool:
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()


def regular_price_from_text(lines: list[ParsedLine], receipt_text: str) -> None:
    """Restore the regular price the model left out of a loyalty-card layout.

    fold_regular_prices fixes the layout when the model reads the regular-price
    row. When it leaves that row out, the item stays at what was paid and the
    saving beneath it is subtracted again. The receipt text still has the row:
    if the row printed just above the saving is a regular price, and that price
    less the saving is what the item was read at, the item takes the regular
    price and keeps the saving, as fold_regular_prices would have done.
    """
    rows = _rows(receipt_text)
    if not rows:
        return
    by_seq = {line.seq: line for line in lines}
    for line in lines:
        if line.line_kind != "discount" or line.parent_seq is None:
            continue
        item = by_seq.get(line.parent_seq)
        if item is None or item.line_kind != "item" or "regular_price_from_text" in item.flags:
            continue
        if any(_REGULAR_PRICE.search(other.raw_text) for other in lines):
            continue  # fold_regular_prices saw the layout; it has decided
        at = [i for i, row in enumerate(rows) if _same_row(row, line.raw_text)]
        if len(at) != 1 or not _SAVING.search(rows[at[0]]):
            continue
        for row in rows[max(at[0] - 2, 0) : at[0]]:
            if not _REGULAR_PRICE.search(row):
                continue
            regular = _amounts(row)
            gap = None if not regular else regular[0] - line.line_total - item.line_total
            if gap is not None and abs(gap) <= RECONCILE_TOLERANCE:
                item.line_total = regular[0]
                if (
                    item.unit_price is not None
                    and item.qty is not None
                    and abs(item.unit_price * item.qty - item.line_total) > RECONCILE_TOLERANCE
                ):
                    item.unit_price = None  # the printed rate was the sale rate
                item.flags.append("regular_price_from_text")
            break


# --- 6. Footer sentences read as items --------------------------------------

_FOOTER_WORDS = re.compile(
    r"\b(?:toward|towards|your|you|free|earn(?:ed)?|points?|thank|visit|survey|reward|"
    r"member|receipt|return|policy|www|com)\b",
    re.IGNORECASE,
)


def flag_footer_rows(lines: list[ParsedLine]) -> None:
    """Flag items at zero that print no amount and read as a sentence.

    A loyalty or footer sentence ("... toward your free sandwich") is not a
    purchase. It is flagged ``footer_text`` only when it prints no amount at all
    (a free item prints its 0.00), is not a weight or count row, and reads as a
    sentence: footer wording, or five words or more. It is not dropped: receipt
    text is untrusted, and a reviewer should see every row the model returned,
    an injected instruction included. Money is unchanged either way.
    """
    for line in lines:
        text = line.raw_text
        if (
            line.line_kind == "item"
            and line.line_total == 0
            and not prints_an_amount(text)
            and quantity_only(text) is None
            and (_FOOTER_WORDS.search(text) or len(re.findall(r"[A-Za-z]{2,}", text)) >= 5)
        ):
            line.flags.append("footer_text")


# --- 7. A tax line read at its base -----------------------------------------

_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def tax_from_rate(lines: list[ParsedLine]) -> None:
    """A tax line printing "<base> @ <rate>% = <amount>" takes the amount.

    Only when the line was read at an amount it prints and the base at the
    rate comes, to the cent, to another amount it prints.
    """
    for line in lines:
        if line.line_kind != "tax":
            continue
        rate = _PERCENT.search(line.raw_text)
        if rate is None:
            continue
        percent = Decimal(rate.group(1).replace(",", "."))
        rest = line.raw_text[: rate.start()] + " " + line.raw_text[rate.end() :]
        amounts = _amounts(rest)
        if line.line_total not in amounts:
            continue
        base = line.line_total
        for amount in amounts:
            if amount != base and abs(base * percent / 100 - amount) <= CENTS:
                line.line_total = amount
                line.flags.append("tax_from_rate")
                break


__all__ = [
    "STRUCTURE_FLAGS",
    "PostPasses",
    "flag_footer_rows",
    "items_read_as_discounts",
    "post_passes",
    "quantity_prefix",
    "regular_price_from_text",
    "tax_from_rate",
    "unjoined_quantity_rows",
]
