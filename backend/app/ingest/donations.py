"""A donation added at the till, read as a discount (pattern 15).

Split out of :mod:`app.ingest.structure`, which runs the pass in its order.
Pure: no I/O.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.ingest.lines import ParsedLine, reconcile

# Wording of money given at the till: "STORE DONATION 0.40", "ROUND UP FOR
# CHARITY 0.37". The reader takes it for money off, since it is not a product.
_DONATION = re.compile(r"\bdonat|\bcharit|\bround\s*-?\s*up\b", re.IGNORECASE)


def _difference(
    lines: list[ParsedLine], printed_total: Decimal, header_tax: Decimal | None
) -> Decimal:
    return abs(Decimal(reconcile(lines, printed_total, header_tax)["difference"]))


def donations_paid(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None
) -> None:
    """A donation read as a discount or an item is a fee: the shopper paid it.

    Read as an item, the money is already right and only the kind changes, so
    the line is not offered as a product to identify. Read as a discount, only
    when the receipt then adds up more closely to its printed total; without
    one, nothing changes. The line is flagged ``donation_paid``.
    """
    for line in lines:
        if line.line_kind == "item" and line.line_total > 0 and _DONATION.search(line.raw_text):
            line.line_kind = "fee"
            line.flags.append("donation_paid")
    if printed_total is None:
        return
    for line in lines:
        if line.line_kind != "discount" or not _DONATION.search(line.raw_text):
            continue
        before = _difference(lines, printed_total, header_tax)
        line.line_kind = "fee"
        if _difference(lines, printed_total, header_tax) >= before:
            line.line_kind = "discount"
            continue
        line.parent_seq = None
        line.flags = [f for f in line.flags if f not in ("parent_inferred", "parent_rejected")]
        line.flags.append("donation_paid")
