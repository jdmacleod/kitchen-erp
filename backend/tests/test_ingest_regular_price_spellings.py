"""OCR's other misreadings of "Regular" still mark a shelf-price row.

Regression: ISSUE-007, found by /qa on 2026-09-28. On a real receipt the "g"
of "Regular" came back as another letter, the rows were read as discounts, and
the receipt never reconciled. Values here are invented.
"""

from decimal import Decimal

import pytest

from app.ingest import lines as lines_mod


def _row(seq: int, raw: str, total: str, kind: str = "item", parent: int | None = None):
    line = lines_mod.ParsedLine(
        seq=seq,
        raw_text=raw,
        line_kind=kind,
        qty=Decimal("1") if kind == "item" else None,
        unit="each" if kind == "item" else None,
        unit_price=None,
        line_total=Decimal(total),
    )
    line.parent_seq = parent
    return line


@pytest.mark.parametrize("word", ["Reaular", "Reqular", "REAULAR"])
def test_misread_regular_price_row_folds_into_the_item(word):
    # The model read the shelf price as a discount; it is still a shelf price.
    lines = [
        _row(1, "COASTAL ORZO 1.10 S", "1.10"),
        _row(2, f"{word} Price 1.50", "1.50", kind="discount", parent=1),
        _row(3, "Card Savings 0.40-", "0.40", kind="discount", parent=1),
    ]
    kept, dropped = lines_mod.fold_regular_prices(lines)
    assert dropped == [f"{word} Price 1.50"]
    assert [(k.line_kind, k.line_total, k.parent_seq) for k in kept] == [
        ("item", Decimal("1.50"), None),
        ("discount", Decimal("0.40"), 1),
    ]
