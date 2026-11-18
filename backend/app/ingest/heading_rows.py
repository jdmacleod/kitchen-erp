"""A line the model read from a row that prints no amount, with the amount below it.

Split out of :mod:`app.ingest.structure`, which runs the pass in its order.
Pure: no I/O.
"""

from __future__ import annotations

from app.ingest.lines import ParsedLine, prints_an_amount
from app.ingest.printed_rows import receipt_rows
from app.ingest.weights import _amounts, quantity_only


def amount_from_row_below(
    lines: list[ParsedLine], receipt_text: str, rows_at: dict[int, int]
) -> None:
    """Join a line read from a row with no amount to the row below that prints it.

    The model can give a department heading the amount of the row under it::

        General
        Charity Round Up         0.40

    and an item whose name runs onto a second row comes out the same way. The
    line keeps the heading's text and takes the row below's, so the row that
    holds the money is the one the line names. Only when that row prints exactly
    the line's amount and nothing else does, it is not a weight or count (a name
    row over its weight is joined by ``merge_quantity_lines``), and no other line
    was read from it. The amount is never changed. The line is flagged
    ``joined_row_below``.
    """
    rows = receipt_rows(receipt_text)
    taken = set(rows_at.values())
    for line in lines:
        at = rows_at.get(line.seq)
        if line.line_kind != "item" or at is None or prints_an_amount(rows[at]):
            continue
        below = at + 1
        if below >= len(rows) or below in taken or quantity_only(rows[below]) is not None:
            continue
        if _amounts(rows[below]) != [line.line_total]:
            continue
        line.raw_text = f"{line.raw_text} {rows[below]}"
        line.printed_row = rows[below]
        rows_at[line.seq] = below
        taken.add(below)
        line.flags.append("joined_row_below")
