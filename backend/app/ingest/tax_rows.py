"""Tax lines fixed from the receipt's printed tax rows (patterns 7 and 7a).

Split out of :mod:`app.ingest.structure`, which runs both passes in its order.
Pure: no I/O.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.ingest.lines import CENTS, ParsedLine, _amounts, printed_text

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


# A row of the tax table: "13.99 @ 11.000% = 1.53", with or without a word before it.
_TAX_AT_RATE = re.compile(
    r"(?<![\d.,])(\d+[.,]\d{2})\s*@\s*(\d+(?:[.,]\d+)?)\s*%\s*=?\s*(\d+[.,]\d{2})(?![\d.,])"
)


# A percentage row worded as a saving: "Discount 10.00 @ 10% = 1.00" takes money off.
_SAVING_WORDING = re.compile(
    r"sav|disc|coupon|cpn|\boff\b|promo|mark\s*down|mkdn|price\s*cut", re.IGNORECASE
)


def _squeezed(text: str) -> str:
    return " ".join(text.split())


def _decimal(token: str) -> Decimal:
    return Decimal(token.replace(",", "."))


def _rows(text: str) -> list[str]:
    return [row for row in text.splitlines() if row.strip()]


def tax_from_print(lines: list[ParsedLine], receipt_text: str, header_tax: Decimal | None) -> None:
    """Add the tax the receipt prints when neither the lines nor the header hold it.

    Some tills print tax as a table, one "<base> @ <rate>% = <amount>" row per
    rate, and the model sometimes reads the items and leaves that table out.
    The rows are only trusted when each base at its rate comes to its amount,
    to the cent, and the bases together come to the items read (before or after
    their savings), so a misread base or a missing item adds nothing. Each row
    that charges tax becomes a tax line; a 0% row adds none. A row already read
    as a line, or worded as a saving, is not part of the table.
    """
    if header_tax is not None or any(line.line_kind == "tax" for line in lines):
        return
    read = {_squeezed(printed_text(line)) for line in lines} | {
        _squeezed(line.raw_text) for line in lines
    }
    found: list[tuple[str, Decimal, Decimal]] = []
    for row in _rows(receipt_text):
        match = _TAX_AT_RATE.search(row)
        if match is None or _squeezed(row) in read or _SAVING_WORDING.search(row):
            continue
        base, percent, amount = (_decimal(g) for g in match.groups())
        if abs(base * percent / 100 - amount) > CENTS:
            return  # a row that does not add up: trust none of them
        found.append((row.strip(), base, amount))
    if not found:
        return
    items = sum((line.line_total for line in lines if line.line_kind == "item"), Decimal(0))
    savings = sum((line.line_total for line in lines if line.line_kind == "discount"), Decimal(0))
    bases = sum((base for _, base, _ in found), Decimal(0))
    if all(abs(bases - read) > CENTS for read in (items, items - savings)):
        return
    seq = max((line.seq for line in lines), default=0)
    for row, _, amount in found:
        if amount == 0:
            continue
        seq += 1
        lines.append(
            ParsedLine(
                seq=seq,
                raw_text=row,
                line_kind="tax",
                qty=None,
                unit=None,
                unit_price=None,
                line_total=amount,
                flags=["tax_from_print"],
                printed_row=row,
            )
        )
