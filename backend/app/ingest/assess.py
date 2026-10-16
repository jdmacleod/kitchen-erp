"""What a reading of a receipt's lines comes to, before anything is written.

The lines stage and a re-read (04, 2S) both take the reader's answer through the
same steps: the deterministic passes, the price checks, the amounts looked for in
the scan, and the reconciliation against the printed total. :func:`assess` is
those steps, pure, so a re-check over a stored answer is the stage's own
judgement and not a copy of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.ingest import lines as lines_stage
from app.ingest import structure
from app.ingest.lines import ParsedLine
from app.ingest.schemas import ReceiptLines
from app.ingest.witness import appears_in

# The purchase flags that come from reading the lines, as against the header or
# the scan. A re-read replaces these and leaves every other flag alone.
LINE_READING_FLAGS = frozenset(
    {
        "lines_unparsed",
        "lines_partial",
        "rows_not_read",
        "total_not_in_scan",
        "reconcile_mismatch",
        "decimals_restore_total",
    }
)

# The line rules: what :func:`assess` makes of one reader's answer. A reading
# records the version it was assessed under (``rules_version`` in its ``lines``
# stage result and in a re-read's proposal), so the drafts read under older rules
# can be found and re-checked together (issue 382). Bump it with any change that
# can turn the same answer into different lines, flags or sums; a change only to
# the reader's prompt is a parser version (lines.GENERIC_PARSER_VERSION) instead,
# since only Read again reaches it. A reading without the key is older than 1.
# 1: printed rows, savings already in a price and the receipt's own checks (#376),
#    codes from the printed row (#379), a rate that names its currency (#383).
LINE_RULES_VERSION = 1


@dataclass
class Assessed:
    """The lines after every pass, the flags the reading raises, and the sums."""

    passes: structure.PostPasses
    reconciliation: dict[str, Any]
    flags: list[str] = field(default_factory=list)  # purchase flags, in the stage's order

    @property
    def lines(self) -> list[ParsedLine]:
        return self.passes.lines


def assess(
    result: ReceiptLines | None,
    receipt_text: str,
    printed_total: Decimal | None,
    header_tax: Decimal | None,
    *,
    unread_parts: list[int] | None = None,
) -> Assessed:
    passes = (
        structure.PostPasses([])
        if result is None
        else structure.post_passes(result, receipt_text, printed_total, header_tax)
    )
    parsed = passes.lines
    flags: list[str] = []
    if result is None:
        flags.append("lines_unparsed")
    elif unread_parts:
        # Some parts answered and some did not: keep what was read, and say that
        # lines are missing rather than let the receipt look complete (#60).
        flags.append("lines_partial")
    if passes.unread_rows:
        # Rows the scan prints like purchases that no line accounts for (#181).
        flags.append("rows_not_read")
    lines_stage.check_prices(parsed, printed_total)
    lines_stage.flag_amounts_not_in_scan(parsed, receipt_text)
    if printed_total is not None and not appears_in(printed_total, receipt_text):
        flags.append("total_not_in_scan")
    reconciliation = lines_stage.reconcile(parsed, printed_total, header_tax)
    if reconciliation["mismatch"]:
        flags.append("reconcile_mismatch")
        if lines_stage.restoring_decimals_reconciles(parsed, printed_total, header_tax):
            flags.append("decimals_restore_total")
    return Assessed(passes, reconciliation, flags)
