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
    QTY_FLAGS,
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
# negative_from_text: an item whose amount is printed with a trailing minus
#   ("10.00-") is a saving, and became a discount (pattern 8).
# points_not_money: a loyalty-points count ("1300 PTS") read as an amount counts
#   for nothing (pattern 9).
# payment_row: a payment ("GIFT CARD 18.10", "BAL 78.20") read as an item counts
#   for nothing, and the receipt adds up more closely without it (pattern 10).
# continuation_row: a row printed under an item that is part of it (its multi-buy
#   rate, its name in another script, its code) was read as a second item at the
#   same amount; it counts for nothing (pattern 11).
# rate_note: a row that is only an "@ rate" note, its rate read as a discount,
#   counts for nothing (pattern 12).
# saving_already_netted: a "you saved" note under an item printed at what was
#   paid was subtracted again; it counts for nothing, and only when the receipt
#   then adds up to its printed total (pattern 13).
STRUCTURE_FLAGS = frozenset(
    {
        "qty_from_prefix",
        "no_amount_printed",
        "kind_from_wording",
        "regular_price_from_text",
        "footer_text",
        "tax_from_rate",
        "negative_from_text",
        "points_not_money",
        "payment_row",
        "continuation_row",
        "rate_note",
        "saving_already_netted",
    }
)


@dataclass
class PostPasses:
    """The lines after every pass, and the rows taken out on the way."""

    lines: list[ParsedLine]
    merged_rows: list[str] = field(default_factory=list)  # weight/count rows joined to items
    dropped_rows: list[str] = field(default_factory=list)  # shelf-price rows (#64)
    # Rows the scan prints like a purchase that no line accounts for (pattern 5).
    unread_rows: list[str] = field(default_factory=list)


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
    continuation_rows(parsed, printed_total, header_tax)
    regular_price_from_text(parsed, receipt_text)
    savings_printed_negative(parsed, receipt_text)
    points_not_money(parsed)
    flag_footer_rows(parsed)
    unjoined_quantity_rows(parsed)
    tax_from_rate(parsed)
    rate_notes_not_discounts(parsed)
    items_read_as_discounts(parsed, printed_total, header_tax)
    savings_already_netted(parsed, printed_total, header_tax)
    payment_rows(parsed, printed_total, header_tax)
    unread = rows_not_read(parsed, receipt_text, merged + dropped, printed_total)
    return PostPasses(_renumber(parsed), merged, dropped, unread)


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
        if printed.unit_misread:
            line.flags.append("unit_misread")
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
# An amount with a till's one-letter tax code after it, at the end of the row.
_TAX_LETTER_END = re.compile(r"\d[.,]\d{2}\s+[A-Z]\s*$")


def _difference(
    lines: list[ParsedLine], printed_total: Decimal, header_tax: Decimal | None
) -> Decimal:
    return abs(Decimal(reconcile(lines, printed_total, header_tax)["difference"]))


def items_read_as_discounts(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None = None
) -> None:
    """A "discount" that names a product becomes an item.

    A product shows by its pack size or by the tax letter a till prints after an
    item's amount. Only with the printed total to check against: the receipt must
    add up more closely with the line as an item than as a discount. A line printed
    negative, or worded as a saving, stays a discount.
    """
    if printed_total is None:
        return
    for line in lines:
        if line.line_kind != "discount" or line.line_total <= 0:
            continue
        if "negative_from_text" in line.flags:
            continue  # the print showed it negative: a saving, whatever the total says
        text = line.raw_text
        if _SAVING_WORDS.search(text) or _NEGATIVE.search(text):
            continue
        # What names a product: its pack size, or the tax letter a till prints after
        # an item's amount (read as a third decimal digit when OCR took it for one).
        if not (
            _PACK_SIZE.search(text)
            or _TAX_LETTER_END.search(text)
            or "tax_code_as_digit" in line.flags
        ):
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


# --- 12. A rate note read as a discount -------------------------------------

# "@ 0.45", "@ $2.40/oz": a rate, with a comma for a decimal point too.
_AT_RATE = re.compile(r"@\s*\$?\s*(\d*[.,]\d{1,2}|\d+)(?!\d)")


def rate_notes_not_discounts(lines: list[ParsedLine]) -> None:
    """A "discount" that is only an "@ rate" note counts for nothing.

    A count or weight row ("3 @ 0.45", "4 oz @ 2.40/oz") that OCR garbled past
    what merge_quantity_lines can read is sometimes read as a discount of its
    rate. When the line's amount is the rate printed after its "@", and it uses
    no saving wording, it is a note about the item above, not a saving: it is
    kept as an item at zero for review, flagged ``rate_note``.
    """
    for line in lines:
        if line.line_kind != "discount" or _SAVING_WORDS.search(line.raw_text):
            continue
        rates = [Decimal(m.group(1).replace(",", ".")) for m in _AT_RATE.finditer(line.raw_text)]
        if line.line_total <= 0 or line.line_total not in rates:
            continue
        line.line_kind = "item"
        line.line_total = Decimal("0")
        line.parent_seq = None
        line.qty = line.unit = line.unit_price = None
        line.flags = [f for f in line.flags if f not in ("parent_inferred", "parent_rejected")]
        line.flags.append("rate_note")


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
        if _difference(lines, printed_total, header_tax) <= RECONCILE_TOLERANCE:
            return  # the receipt adds up: every saving is needed
        saving = line.line_total
        line.line_total = Decimal("0")
        if _difference(lines, printed_total, header_tax) <= RECONCILE_TOLERANCE:
            line.flags.append("saving_already_netted")
        else:
            line.line_total = saving


# --- 5. Rows the model skipped ---------------------------------------------

# A row priced like a purchase: an amount at its end with at most a tax letter or
# two after it, or digits that lost their decimal point followed by a tax letter.
_PRICED_ROW = re.compile(r"(?:\d+[.,]\d{2}\s*[A-Za-z*]{0,2}|(?<![\d.,])\d{3,4}\s*[A-Z])\s*$")
_WORD = re.compile(r"[A-Za-z]{3,}")
# Rows that are not purchases even when priced: totals, tax, payment and change,
# the store's own details, loyalty points and savings summaries. OCR's 0 for O
# and 1 for l are allowed in the total words.
_NOT_A_PURCHASE = re.compile(
    r"\b(?:sub\s*t[o0]ta[l1]|t[o0]ta[l1]|tax|ba[l1]ance|bal|change|cash|visa|master|amex|debit|credit|"
    r"tend\w*|amount|approv\w*|account|auth|payment|paid|store|term\w*|trans\w*|tel\w*|"
    r"phone|points?|pts|reward\w*|sav\w*|you|card)\b"
    r"|[#%=]|\(\d{3}\)|\d{3}-\d{4}|\d:\d{2}",
    re.IGNORECASE,
)
_MAX_UNREAD = 20


def _key(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())


def rows_not_read(
    lines: list[ParsedLine],
    receipt_text: str,
    taken: list[str],
    printed_total: Decimal | None = None,
) -> list[str]:
    """Rows printed like a purchase that no line accounts for (pattern 5).

    The model sometimes leaves a row out, most often one whose amount OCR
    garbled. Nothing is invented for it: the rows are only listed, so review
    can say that some printed rows were not read. A row counts when it lies
    between the first and last rows that were read, has a word and an amount
    (or the digits of one, with a tax letter) at its end, is not a total, tax,
    payment, store-detail, points or savings row, does not print an amount
    within a tenth of the receipt's total or at least everything read (the
    total or a payment, under a name OCR garbled), and matches no line that was
    read or row a pass took out.
    """
    rows = _rows(receipt_text)
    if not rows or not lines:
        return []
    keys = [_key(row) for row in rows]
    read = [_key(text) for text in [line.raw_text for line in lines] + taken]
    read = [k for k in read if k]

    def accounted(i: int) -> bool:
        return any(k == keys[i] or (len(k) > 6 and (k in keys[i] or keys[i] in k)) for k in read)

    read_items = sum((line.line_total for line in lines if line.line_kind == "item"), Decimal(0))
    seen = [i for i in range(len(rows)) if accounted(i)]
    if not seen:
        return []
    unread: list[str] = []
    for i in range(seen[0] + 1, seen[-1]):
        row = rows[i]
        if accounted(i) or not _WORD.search(row) or not _PRICED_ROW.search(row):
            continue
        if _NOT_A_PURCHASE.search(row) or _REGULAR_PRICE.search(row) or quantity_only(row):
            continue
        amount = (_amounts(row) or [None])[-1]
        if amount is not None and (
            amount >= read_items
            or (printed_total and abs(amount - printed_total) * 10 <= printed_total)
        ):
            continue  # the total or a payment of it, under a name OCR garbled
        unread.append(row.strip())
        if len(unread) == _MAX_UNREAD:
            break
    return unread


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


# --- 8. A saving printed negative, read as an item ---------------------------


def _money(amount: Decimal) -> str:
    return f"{amount.quantize(CENTS)}"


def _prints_negative(row: str, amount: Decimal) -> bool:
    """Whether the row prints ``amount`` as a negative: "10.00-" or "-10.00"."""
    figure = re.escape(_money(amount))
    return bool(re.search(rf"(?<![\d.]){figure}\s*-|-\s*\$?{figure}(?![\d])", row))


def _rows_with(receipt_text: str, raw_text: str) -> list[str]:
    wanted = " ".join(raw_text.split()).lower()
    return [row for row in _rows(receipt_text) if wanted in " ".join(row.split()).lower()]


def savings_printed_negative(lines: list[ParsedLine], receipt_text: str = "") -> None:
    """An item whose amount is printed negative is a saving: it becomes a discount.

    A till prints a saving as "10.00-" (or "-10.00"), often on a row of its own
    beneath the item. Read as an item, it was added instead of subtracted. The
    proof is the print: the line's own text shows its amount negative, or the one
    row of the receipt text that holds the line's text does. The discount
    attaches to the item printed above it.
    """
    for i, line in enumerate(lines):
        if line.line_kind != "item" or line.line_total <= 0:
            continue
        rows = [line.raw_text] if prints_an_amount(line.raw_text) else []
        if not rows and receipt_text:
            rows = _rows_with(receipt_text, line.raw_text)
        if len(rows) != 1 or not _prints_negative(rows[0], line.line_total):
            continue
        line.line_kind = "discount"
        line.qty = line.unit = line.unit_price = None
        line.flags = [f for f in line.flags if f not in QTY_FLAGS]
        line.flags.append("negative_from_text")
        above = next((p for p in reversed(lines[:i]) if p.line_kind == "item"), None)
        if above is not None:
            line.parent_seq = above.seq
            line.flags.append("parent_inferred")


# --- 9. Loyalty points read as money ----------------------------------------

_POINTS = re.compile(r"\b(?:pts|points?)\b", re.IGNORECASE)
_EARNED = re.compile(r"\b(?:earn\w*|bonus|get|collected|balance)\b", re.IGNORECASE)
_REDEEMED = re.compile(r"\b(?:redeem\w*|redemption|reward|used|applied)\b", re.IGNORECASE)


def _is_points_count(text: str, amount: Decimal) -> bool:
    """Whether ``amount`` is a whole number the text speaks of as points.

    Money is printed with its cents; a points count is not. Either the number
    stands beside the points word ("1300 PTS", "POINTS EARNED 125"), or the
    text prints no number at all and speaks of points earned ("POINTS EARNED",
    the count read from the column beside it). Points redeemed are money off,
    and a number printed with cents is never points.
    """
    if amount != amount.to_integral_value() or amount <= 0 or _REDEEMED.search(text):
        return False
    if not re.search(r"\d", text):
        return bool(_EARNED.search(text))
    whole = re.escape(str(int(amount)))
    bare = rf"(?<![\d.,]){whole}(?![\d]|[.,]\d)"
    if re.search(rf"(?<![\d.,]){whole}[.,]\d{{2}}", text):
        return False
    return bool(
        re.search(rf"{bare}\s*(?:pts|points?)\b", text, re.IGNORECASE)
        or re.search(rf"\b(?:pts|points?)\b[^\d$]{{0,20}}{bare}", text, re.IGNORECASE)
    )


def points_not_money(lines: list[ParsedLine]) -> None:
    """A loyalty-points count read as an amount counts for nothing.

    "Spend $125 get 1300PTS 1300 PTS" read as a 1,300 discount, or "POINTS
    EARNED 125" as a 125 one, took the receipt far below its total. The row is
    kept, as an item at zero, for review to see.
    """
    for line in lines:
        if line.line_total <= 0 or line.line_kind == "tax":
            continue
        if not _POINTS.search(line.raw_text) or not _is_points_count(
            line.raw_text, line.line_total
        ):
            continue
        line.line_kind = "item"
        line.line_total = Decimal("0")
        line.parent_seq = None
        line.unit_price = None
        line.flags.append("points_not_money")
    _detach_from(lines, "points_not_money")


def _detach_from(lines: list[ParsedLine], flag: str) -> None:
    """Nothing hangs off a row that is not a purchase of its own."""
    for line in lines:
        parent = next((p for p in lines if p.seq == line.parent_seq), None)
        if parent is not None and flag in parent.flags:
            line.parent_seq = None


# --- 10. A payment read as an item ------------------------------------------

# How a till names a payment, a balance or change. A purchased gift card prints
# ACTIVATED or ACTIVATION, and is an item.
_PAYMENT = re.compile(
    r"\b(?:gift\s*card|shop\s*card|tender(?:ed)?|cash|change(?:\s+due)?|visa|master\s*card|"
    r"amex|debit|credit|interac|bal(?:ance)?|amount\s+paid|paid|approved)\b",
    re.IGNORECASE,
)
_ACTIVATED = re.compile(r"activat", re.IGNORECASE)


def payment_rows(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None = None
) -> None:
    """A payment read as an item counts for nothing, when the receipt agrees.

    Tender, gift-card and balance rows printed below the total are how the
    receipt was paid, not what was bought. Read as items, they added the total
    again. Worded as a payment and never as a purchased card, each such item
    goes to zero only when the receipt then adds up more closely to its printed
    total; without one, nothing changes. The row is kept for review.
    """
    if printed_total is None:
        return
    for line in lines:
        if line.line_kind != "item" or line.line_total <= 0:
            continue
        text = line.raw_text
        if not _PAYMENT.search(text) or _ACTIVATED.search(text):
            continue
        before = _difference(lines, printed_total, header_tax)
        amount = line.line_total
        line.line_total = Decimal("0")
        if _difference(lines, printed_total, header_tax) < before:
            line.unit_price = None
            line.flags.append("payment_row")
        else:
            line.line_total = amount
    _detach_from(lines, "payment_row")


# --- 11. A row of an item read as another item -------------------------------

# "2 @ 1/ $8.99", "690245 2 @2/$4.47", "2 @ $3.49ea.": a count, an optional "for
# how many", and a price. The line comes to count x price / for-how-many.
_MULTI_BUY = re.compile(
    r"(?<![\d.])(?P<count>\d{1,3})\s*@\s*(?:(?P<per>\d{1,2})\s*/\s*)?\$?\s*"
    r"(?P<price>\d+[.,]\d{2})"
)
# Letters of a script other than Latin: Cyrillic, Hebrew, Arabic, Thai, kana, CJK,
# Hangul. A till that prints a name twice prints it in two scripts.
_NON_LATIN = re.compile(
    "[\u0400-\u04ff\u0590-\u06ff\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]"
)
_NAME_WORD = re.compile(r"[A-Za-z]{3,}")


def _multi_buy(raw_text: str, amount: Decimal) -> tuple[Decimal, Decimal | None] | None:
    """(count, unit price) when the row is a rate, naming nothing, that comes to
    ``amount``. A row that names a product beside its rate is a purchase."""
    m = _MULTI_BUY.search(raw_text)
    if m is None:
        return None
    rest = raw_text[: m.start()] + " " + raw_text[m.end() :]
    if _NAME_WORD.search(re.sub(r"\b(?:each|for|ea)\b", " ", rest, flags=re.IGNORECASE)):
        return None
    count = Decimal(m["count"])
    per = Decimal(m["per"] or "1")
    price = Decimal(m["price"].replace(",", "."))
    if count < 1 or per < 1 or abs(count * price / per - amount) > CENTS:
        return None
    each = price / per
    return count, each if each == each.quantize(CENTS) else None


def _part_of(row: ParsedLine, head: ParsedLine) -> bool:
    """Whether ``row`` reads as part of ``head`` rather than a purchase of its own:
    its name in another script, or a code or size with no name in it."""
    raw = row.raw_text
    if _NON_LATIN.search(raw):
        return not _NON_LATIN.search(head.raw_text)
    return not _NAME_WORD.search(raw)


def continuation_rows(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None = None
) -> None:
    """Rows printed under an item that belong to it count for nothing.

    A till can print one purchase over several rows: the name, then its
    multi-buy rate ("2 @ 1/ $8.99"), its name again in another script, its code
    or size. Read row by row, each row became an item carrying the purchase's
    amount, and the receipt counted it two to five times. A row is part of the
    item above when it carries the same amount and:

    * prints a rate that comes to that amount (the arithmetic is the proof; the
      item takes the count and the unit price), or
    * is the name in another script, or a code or size with no name in it, and
      the receipt then adds up more closely to its printed total (without one,
      nothing changes).

    Two rows that both name a product are two purchases, even at one price. The
    row is kept for review as an item at zero; a saving attached to it moves to
    the item.
    """
    head: ParsedLine | None = None
    for line in lines:
        # Savings, taxes and rows at zero (a weight row) sit between an item and
        # its rows; only an item with an amount can be a head or a row of one.
        if line.line_kind != "item" or line.line_total <= 0:
            continue
        if head is None or line.line_total != head.line_total:
            head = line
            continue
        rate = _multi_buy(line.raw_text, line.line_total)
        if rate is None:
            if not _part_of(line, head) or printed_total is None:
                head = line  # a second purchase at the same price, or no way to tell
                continue
            before = _difference(lines, printed_total, header_tax)
            amount, line.line_total = line.line_total, Decimal("0")
            if _difference(lines, printed_total, header_tax) >= before:
                line.line_total = amount
                head = line
                continue
        else:
            line.line_total = Decimal("0")
            if head.qty in (None, Decimal("1")) or "qty_assumed" in head.flags:
                head.qty, head.unit, head.unit_price = rate[0], "each", rate[1]
                head.flags = [f for f in head.flags if f not in QTY_FLAGS]
                head.flags.append("qty_from_line_below")
        line.unit_price = None
        line.flags = [f for f in line.flags if f not in QTY_FLAGS] + ["continuation_row"]
        for other in lines:
            if other.parent_seq == line.seq:
                other.parent_seq = head.seq


__all__ = [
    "STRUCTURE_FLAGS",
    "PostPasses",
    "flag_footer_rows",
    "continuation_rows",
    "items_read_as_discounts",
    "payment_rows",
    "points_not_money",
    "post_passes",
    "quantity_prefix",
    "regular_price_from_text",
    "savings_printed_negative",
    "tax_from_rate",
    "unjoined_quantity_rows",
]
