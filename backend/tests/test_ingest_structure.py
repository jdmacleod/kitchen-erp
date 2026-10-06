"""Line-structure passes (#181).

Every receipt here is invented: item words, amounts and layouts.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ingest import structure
from app.ingest.schemas import ReceiptLine, ReceiptLines


def _model(*rows: tuple) -> ReceiptLines:
    """What the model answered: (raw_text, kind, total[, parent_index[, qty]])."""
    return ReceiptLines(
        lines=[
            ReceiptLine(
                raw_text=row[0],
                line_kind=row[1],
                line_total=Decimal(row[2]),
                parent_index=row[3] if len(row) > 3 else None,
                qty=Decimal(row[4]) if len(row) > 4 else None,
            )
            for row in rows
        ]
    )


def _passes(*rows: tuple, text: str = "", total: str | None = None):
    return structure.post_passes(
        _model(*rows), text, None if total is None else Decimal(total)
    ).lines


def _by_text(lines):
    return {line.raw_text: line for line in lines}


# --- 1. A count printed before the name -------------------------------------


def test_a_qty_prefix_gives_the_count_and_the_unit_price():
    (line,) = _passes(("3 QTY MOSSGLEN FARFALLE 4.47", "item", "4.47"))
    assert (line.qty, line.unit, line.unit_price, line.line_total) == (
        Decimal("3"),
        "each",
        Decimal("1.49"),
        Decimal("4.47"),
    )
    assert line.flags == ["qty_from_prefix"]


def test_a_qty_prefix_with_two_amounts_takes_the_extended_one():
    # The model took the unit price as the line total.
    (line,) = _passes(("QTY 2 KESTREL SODA 1.15 2.30", "item", "1.15"))
    assert (line.qty, line.unit_price, line.line_total) == (
        Decimal("2"),
        Decimal("1.15"),
        Decimal("2.30"),
    )


def test_a_count_that_does_not_divide_to_the_cent_leaves_the_unit_price_unknown():
    (line,) = _passes(("3 QTY WREN LIMES 1.00", "item", "1.00"))
    assert line.qty == Decimal("3") and line.unit_price is None
    assert line.line_total == Decimal("1.00")


@pytest.mark.parametrize(
    "raw",
    ["1 QTY MOSSGLEN FARFALLE 1.49", "2 X 12OZ HEARTH BEANS 3.10", "QTYMASTER TONGS 6.00"],
)
def test_other_rows_keep_their_reading(raw):
    (line,) = _passes((raw, "item", raw.split()[-1]))
    assert "qty_from_prefix" not in line.flags


# --- 2. A weight or count row that could not be joined ----------------------


def test_an_unjoined_count_row_read_as_a_discount_counts_for_nothing():
    lines = _passes(
        ("BRAMBLE PEARS 6.12", "item", "6.12"),
        ("4 @ 0.65", "discount", "0.65"),  # 2.60 is not 6.12: no join
    )
    row = _by_text(lines)["4 @ 0.65"]
    assert row.line_kind == "item" and row.line_total == Decimal("0")
    assert row.parent_seq is None
    assert {"quantity_line", "no_amount_printed"} <= set(row.flags)
    assert (row.qty, row.unit, row.unit_price) == (Decimal("4"), "each", Decimal("0.65"))


def test_an_unjoined_weight_row_read_at_its_weight_counts_for_nothing():
    lines = _passes(
        ("1.84 lb @ 2.89/lb", "item", "1.84"),
        ("CINDER PLUMS 9.99", "item", "9.99"),
    )
    row = _by_text(lines)["1.84 lb @ 2.89/lb"]
    assert row.line_total == Decimal("0") and "no_amount_printed" in row.flags


def test_a_joinable_count_row_still_joins_its_item():
    lines = _passes(("BRAMBLE PEARS 2.60", "item", "2.60"), ("4 @ 0.65", "discount", "0.65"))
    assert len(lines) == 1 and lines[0].qty == Decimal("4")


# --- 3. An item read as a discount ------------------------------------------


def test_a_sized_product_read_as_a_discount_becomes_an_item_when_it_reconciles():
    lines = _passes(
        ("FENWICK OATS 2.00", "item", "2.00"),
        ("MARSHFIELD JASMINE RICE 20 LB 14.75", "discount", "14.75"),
        total="16.75",
    )
    rice = _by_text(lines)["MARSHFIELD JASMINE RICE 20 LB 14.75"]
    assert rice.line_kind == "item" and rice.parent_seq is None
    assert "kind_from_wording" in rice.flags and "parent_inferred" not in rice.flags


@pytest.mark.parametrize(
    ("raw", "total"),
    [
        ("MEMBER SAVINGS 16 OZ 1.25", "0.75"),  # worded as a saving
        ("TIDEWATER BEANS 16 OZ 1.25-", "0.75"),  # printed negative
        ("TIDEWATER BEANS 1.25", "0.75"),  # no size
    ],
)
def test_a_discount_worded_or_printed_as_one_stays_a_discount(raw, total):
    lines = _passes(("FENWICK OATS 2.00", "item", "2.00"), (raw, "discount", "1.25"), total=total)
    assert _by_text(lines)[raw].line_kind == "discount"


def test_a_sized_discount_stays_when_the_receipt_adds_up_as_read():
    lines = _passes(
        ("FENWICK OATS 2.00", "item", "2.00"),
        ("TIDEWATER BEANS 16 OZ 1.25", "discount", "1.25"),
        total="0.75",
    )
    assert _by_text(lines)["TIDEWATER BEANS 16 OZ 1.25"].line_kind == "discount"


def test_without_a_printed_total_a_discount_is_never_turned():
    lines = _passes(
        ("FENWICK OATS 2.00", "item", "2.00"),
        ("MARSHFIELD JASMINE RICE 20 LB 14.75", "discount", "14.75"),
    )
    assert lines[1].line_kind == "discount"


# --- 4. A net price with its saving subtracted again ------------------------

_LOYALTY = "\n".join(
    [
        "LARKSPUR CHEDDAR 8OZ      3.79 S",
        "Regular Price             4.29",
        "Card Savings              0.50-",
        "TOTAL                     3.79",
    ]
)


def test_a_regular_price_the_model_left_out_is_restored_from_the_text():
    lines = _passes(
        ("LARKSPUR CHEDDAR 8OZ      3.79 S", "item", "3.79"),
        ("Card Savings              0.50-", "discount", "0.50", 0),
        text=_LOYALTY,
    )
    item, saving = lines
    assert item.line_total == Decimal("4.29") and "regular_price_from_text" in item.flags
    assert saving.parent_seq == item.seq
    assert item.line_total - saving.line_total == Decimal("3.79")


def test_a_regular_price_that_does_not_add_up_is_left_alone():
    text = _LOYALTY.replace("4.29", "4.49")
    lines = _passes(
        ("LARKSPUR CHEDDAR 8OZ      3.79 S", "item", "3.79"),
        ("Card Savings              0.50-", "discount", "0.50", 0),
        text=text,
    )
    assert lines[0].line_total == Decimal("3.79") and lines[0].flags == []


# --- 6. Footer sentences read as items --------------------------------------


@pytest.mark.parametrize(
    ("raw", "flagged"),
    [
        ("Earn 2 more visits toward a free pastry", True),
        ("Thank you for shopping with us", True),
        ("FREE TASTING CUP 0.00", False),  # a free item prints its amount
        ("CINDER PLUMS", False),  # a name row
    ],
)
def test_footer_sentences_at_zero_are_flagged_and_kept(raw, flagged):
    (line,) = _passes((raw, "item", "0"))
    assert ("footer_text" in line.flags) is flagged


# --- 7. A tax line read at its base -----------------------------------------


def test_a_tax_line_read_at_its_base_takes_the_amount():
    (line,) = _passes(("SALES TAX 31.40 @ 8.25% = 2.59", "tax", "31.40"))
    assert line.line_total == Decimal("2.59") and line.flags == ["tax_from_rate"]


def test_a_tax_line_read_right_is_left_alone():
    (line,) = _passes(("SALES TAX 31.40 @ 8.25% = 2.59", "tax", "2.59"))
    assert line.line_total == Decimal("2.59") and line.flags == []
