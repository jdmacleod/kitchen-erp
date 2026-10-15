"""What the receipt prints, beside what the model said it prints.

The model is asked for each row exactly as printed, and often answers with the
name alone: "PEPPERS THAI" for "3781 PEPPERS THAI 0.45 0.45 S". The passes that
work from a line's printed numbers then see none. :func:`anchor_rows` matches
each line back to its OCR row, so those passes, and review, can read the row.

:func:`savings_in_price` finds savings a till prints under an item whose price
already includes them (a "Price" and a "You Pay" column), which would otherwise
be subtracted a second time. :func:`printed_checks` reads what a receipt prints
about itself besides its total: its savings total, its item count, and what was
paid.

Receipt text is untrusted: it is matched and searched, never interpreted.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.ingest.lines import (
    CENTS,
    RECONCILE_TOLERANCE,
    ParsedLine,
    _amounts,
    printed_text,
    reconcile,
)

# Flags set here.
# saving_in_price: a saving printed under an item whose two printed amounts
#   (price, then what was paid) differ by exactly the saving; it counts for nothing
#   and keeps its amount in in_price_amount.
# saving_amount_from_row: as saving_in_price, with the saving read one digit off
#   the difference its item prints; the amount kept is the item's difference.
# saving_in_price_by_total: no row proves it, but counting every remaining saving
#   as already in the price is the one reading that matches the printed total
#   (and the printed savings total, when there is one). Kept for a careful look.
IN_PRICE_FLAGS = frozenset(
    {"saving_in_price", "saving_amount_from_row", "saving_in_price_by_total"}
)

# How far ahead of the last matched row a line's row may be. The model reads in
# order; a match further on is more likely the same words printed again.
_LOOKAHEAD = 12
_MIN_KEY = 3
_SAVING_WORDING = re.compile(r"sav|disc|coupon|member|card|reward|promo|deal|off\b", re.I)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _rows(receipt_text: str) -> list[str]:
    return [" ".join(row.split()) for row in receipt_text.splitlines() if row.strip()]


def anchor_rows(lines: list[ParsedLine], receipt_text: str) -> None:
    """Match each line, in order, to the OCR row that holds its text.

    A row matches when its letters and digits contain the line's, within a few
    rows of the previous match: two "Member Savings" rows match the first and
    then the second. A line with no such row keeps ``printed_row`` None; nothing
    is guessed. The model's ``raw_text`` is never changed.
    """
    rows = _rows(receipt_text)
    keys = [_key(row) for row in rows]
    at = 0
    for line in lines:
        key = _key(line.raw_text)
        if len(key) < _MIN_KEY:
            continue
        for j in range(at, min(at + _LOOKAHEAD, len(rows))):
            if key in keys[j]:
                line.printed_row = rows[j]
                at = j + 1
                break


def _one_digit_off(a: Decimal, b: Decimal) -> bool:
    """Two amounts printed alike but for one digit (1.89 read for 1.99)."""
    sa, sb = f"{a:.2f}", f"{b:.2f}"
    return len(sa) == len(sb) and sum(x != y for x, y in zip(sa, sb, strict=True)) == 1


def _row_saving(item: ParsedLine) -> Decimal | None:
    """The saving an item's row proves: its larger amount less what was paid.

    Only when the row prints exactly two amounts, the second is what the item
    was read at, and the first is larger.
    """
    amounts = _amounts(printed_text(item))
    if len(amounts) != 2:
        return None
    price, paid = amounts
    if abs(paid - item.line_total) > CENTS or price <= paid:
        return None
    return price - paid


def _mark(line: ParsedLine, amount: Decimal, *flags: str) -> None:
    line.in_price_amount = amount
    line.line_total = Decimal("0")
    line.flags.extend(f for f in flags if f not in line.flags)


def savings_in_price(
    lines: list[ParsedLine],
    printed_total: Decimal | None,
    header_tax: Decimal | None = None,
    printed_savings: Decimal | None = None,
) -> None:
    """Count a saving the item's price already includes as nothing, keeping it.

    Per item first: the saving's item prints its price and what was paid, and
    the difference is the saving (or the saving read one digit off it). Then,
    with the printed total to check against, the rest together: if counting
    every remaining saving as already in the price makes the lines match the
    total, and the receipt's printed savings total (when there is one) is what
    all the savings come to, they are marked too, for a careful look.
    """
    _savings_proved_by_rows(lines)
    if printed_total is not None:
        _savings_proved_by_total(lines, printed_total, header_tax, printed_savings)


def _savings_proved_by_rows(lines: list[ParsedLine]) -> None:
    by_seq = {line.seq: line for line in lines}
    for line in lines:
        if line.line_kind != "discount" or line.line_total <= 0 or line.parent_seq is None:
            continue
        item = by_seq.get(line.parent_seq)
        saving = None if item is None or item.line_kind != "item" else _row_saving(item)
        if saving is None:
            continue
        if abs(saving - line.line_total) <= CENTS:
            _mark(line, line.line_total, "saving_in_price")
        elif _one_digit_off(saving, line.line_total):
            _mark(line, saving, "saving_in_price", "saving_amount_from_row")


def _savings_proved_by_total(
    lines: list[ParsedLine],
    printed_total: Decimal,
    header_tax: Decimal | None,
    printed_savings: Decimal | None,
) -> None:
    rest = [
        line
        for line in lines
        if line.line_kind == "discount"
        and line.line_total > 0
        and _SAVING_WORDING.search(printed_text(line))
    ]
    if not rest or _off_by(lines, printed_total, header_tax) <= RECONCILE_TOLERANCE:
        return
    if printed_savings is not None:
        marked = [line.in_price_amount for line in lines if line.in_price_amount is not None]
        all_savings = sum(marked, Decimal("0")) + sum((x.line_total for x in rest), Decimal("0"))
        if abs(all_savings - printed_savings) > RECONCILE_TOLERANCE:
            return
    kept = [line.line_total for line in rest]
    for line in rest:
        line.line_total = Decimal("0")
    adds_up = _off_by(lines, printed_total, header_tax) <= RECONCILE_TOLERANCE
    for line, amount in zip(rest, kept, strict=True):
        line.line_total = amount
        if adds_up:
            _mark(line, amount, "saving_in_price_by_total")


def _off_by(lines: list[ParsedLine], printed_total: Decimal, header_tax: Decimal | None) -> Decimal:
    return abs(Decimal(reconcile(lines, printed_total, header_tax)["difference"]))


# --- 13. A saving already netted into the item's price ----------------------

# How a till words a saving it only reports: "YOU SAVED", "WAS $2.10".
_SAVED_NOTE = re.compile(r"you\s*sav|\bwas\b", re.IGNORECASE)


def savings_already_netted(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None = None
) -> None:
    """A "you saved" note under a net price counts for nothing, when that adds up.

    Some tills print what was paid on the item's row and only report the saving
    below it ("Was $2.10/lb YOU SAVED .66"). Subtracting that saving counts it
    twice. Nothing on the row says which layout it is, so the receipt decides:
    the note counts for nothing only when the lines then add up to the printed
    total, and did not before. Flagged ``saving_already_netted``; never dropped.
    """
    if printed_total is None:
        return
    for line in lines:
        if line.line_kind != "discount" or line.line_total <= 0:
            continue
        if not _SAVED_NOTE.search(line.raw_text) or "negative_from_text" in line.flags:
            continue
        if _off_by(lines, printed_total, header_tax) <= RECONCILE_TOLERANCE:
            return  # the receipt adds up: every saving is needed
        saving = line.line_total
        line.line_total = Decimal("0")
        if _off_by(lines, printed_total, header_tax) <= RECONCILE_TOLERANCE:
            line.flags.append("saving_already_netted")
            line.in_price_amount = saving
        else:
            line.line_total = saving


# --- What the receipt prints about itself ------------------------------------

_SAVINGS_HEAD = re.compile(
    r"\b(?:your\s+savings|total\s+sav\w*|you\s+saved|savings\s+total|total\s+saved)\b", re.I
)
_TOTAL_WORD = re.compile(r"\btotal\b", re.I)
_ITEM_COUNT = re.compile(
    r"\b(?:items?\s+sold|number\s+of\s+items(?:\s+sold)?|item\s+count|total\s+items)\b"
    r"\D{0,12}?(\d{1,3})\b",
    re.I,
)
_PAYMENT = re.compile(
    r"\b(?:payment\s+amount|amount\s+paid|total\s+paid|visa|mastercard|master\s+card|amex|"
    r"american\s+express|discover|debit)\b",
    re.I,
)
_PERCENT_ROW = re.compile(r"\d\s*%")


def _last_amount(row: str) -> Decimal | None:
    amounts = _amounts(row)
    return amounts[-1] if amounts else None


def _savings_total(rows: list[str]) -> Decimal | None:
    """A savings total: on the row that names it, or the next "total" row under it."""
    for i, row in enumerate(rows):
        if not _SAVINGS_HEAD.search(row) or _PERCENT_ROW.search(row):
            continue
        amount = _last_amount(row)
        if amount is not None:
            return amount
        for below in rows[i + 1 : i + 4]:
            if _TOTAL_WORD.search(below) and not _PERCENT_ROW.search(below):
                found = _last_amount(below)
                if found is not None:
                    return found
    return None


def _payment(rows: list[str]) -> Decimal | None:
    """What a card paid, when every card row agrees; cash and change are left out."""
    paid = {amount for row in rows if _PAYMENT.search(row) and (amount := _last_amount(row))}
    return paid.pop() if len(paid) == 1 else None


def printed_checks(receipt_text: str) -> dict[str, Any] | None:
    """The savings total, items sold and payment the receipt prints, as strings.

    Each is read only when the receipt's wording names it, and left out when it
    is missing or ambiguous. None when none of them was found.
    """
    rows = _rows(receipt_text)
    out: dict[str, Any] = {}
    savings = _savings_total(rows)
    if savings is not None:
        out["savings_total"] = str(savings)
    counts = {int(m.group(1)) for row in rows if (m := _ITEM_COUNT.search(row))}
    if len(counts) == 1:
        out["item_count"] = counts.pop()
    payment = _payment(rows)
    if payment is not None:
        out["payment"] = str(payment)
    return out or None
