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


# --- 8. A saving printed negative, read as an item ---------------------------


def test_an_amount_printed_negative_is_a_saving_on_the_item_above():
    lines = _passes(
        ("2203 NORTHWIND PARKA 39.99 A", "item", "39.99"),
        ("4471 SAVING/2203 8.00- A", "item", "8.00"),
        ("1180 LARCH TEA 4.29", "item", "4.29"),
    )
    parka, saving, tea = lines
    assert (saving.line_kind, saving.line_total, saving.parent_seq) == (
        "discount",
        Decimal("8.00"),
        parka.seq,
    )
    assert saving.qty is None and saving.flags == ["negative_from_text", "parent_inferred"]
    assert tea.line_kind == "item" and "negative_from_text" not in tea.flags


def test_the_receipt_text_shows_the_minus_when_the_line_omits_its_amount():
    # A vision or text reader gave the row without its amount; the scan has it.
    text = "2203 NORTHWIND PARKA   39.99 A\n4471 SAVING/2203   8.00- A\nSUBTOTAL 31.99"
    lines = _passes(
        ("2203 NORTHWIND PARKA", "item", "39.99"),
        ("4471 SAVING/2203", "item", "8.00"),
        text=text,
    )
    assert [(line.line_kind, "negative_from_text" in line.flags) for line in lines] == [
        ("item", False),
        ("discount", True),
    ]


def test_a_saving_proven_by_the_print_is_not_turned_back_into_an_item():
    # Named like a product with a size, and the total would favour an item, but the
    # scan prints it negative.
    text = "SLATE COCOA 400G   6.00\nSLATE COCOA 400G OFFER   2.00-"
    lines = _passes(
        ("SLATE COCOA 400G", "item", "6.00"),
        ("SLATE COCOA 400G OFFER", "item", "2.00"),
        text=text,
        total="8.00",
    )
    assert lines[1].line_kind == "discount" and "kind_from_wording" not in lines[1].flags


@pytest.mark.parametrize(
    ("rows", "text"),
    [
        # Printed positive: an item.
        ((("4471 SAVING/2203 8.00 A", "item", "8.00"),), ""),
        # The scan holds the line's text twice: which row is it?
        ((("MOSS SOAP", "item", "3.00"),), "MOSS SOAP 3.00-\nMOSS SOAP 3.00"),
        # A hyphen in a name is not a minus.
        ((("2-PK WREN CANDLES 6.50", "item", "6.50"),), ""),
    ],
)
def test_an_amount_not_proven_negative_stays_an_item(rows, text):
    (line,) = _passes(*rows, text=text)
    assert line.line_kind == "item" and "negative_from_text" not in line.flags


# --- 9. Loyalty points read as money ----------------------------------------


@pytest.mark.parametrize(
    ("raw", "kind", "amount"),
    [
        ("Spend $40 get 500PTS 500 PTS", "discount", "500"),
        ("POINTS EARNED 60", "discount", "60"),
        ("Bonus 25 points", "item", "25"),
        ("POINTS EARNED", "discount", "100"),  # the count printed in the amount column
    ],
)
def test_a_points_count_counts_for_nothing(raw, kind, amount):
    item, points = _passes(("HARBOR OATS 1KG 5.49", "item", "5.49"), (raw, kind, amount))
    assert (points.line_kind, points.line_total, points.parent_seq) == ("item", Decimal("0"), None)
    assert "points_not_money" in points.flags
    assert item.line_total == Decimal("5.49")


@pytest.mark.parametrize(
    ("raw", "amount"),
    [
        ("POINTS REDEEMED 5.00-", "5.00"),  # money, printed with its cents
        ("REWARD SAVING 3.00", "3.00"),  # no points word
        ("2 PT CRATE 12", "12"),  # PT is not points
        ("POINTS DISCOUNT", "2.50"),  # read with cents: money
        ("POINTS REWARD", "5.00"),  # points redeemed are money off
        ("POINTS REDEEMED 500 PTS", "5"),  # even beside a points count
    ],
)
def test_money_beside_points_wording_is_left_alone(raw, amount):
    _, line = _passes(("HARBOR OATS 1KG 5.49", "item", "5.49"), (raw, "discount", amount))
    assert line.line_kind == "discount" and line.line_total == Decimal(amount)
    assert "points_not_money" not in line.flags


# --- 10. A payment read as an item ------------------------------------------


def test_payment_rows_count_for_nothing_when_the_receipt_then_adds_up():
    lines = _passes(
        ("FERN BREAD 12.50", "item", "12.50"),
        ("COPPER JAM 7.50", "item", "7.50"),
        ("GIFT CARD 15.00", "item", "15.00"),
        ("BAL: 22.40", "item", "22.40"),
        ("CASH 5.00", "item", "5.00"),
        total="20.00",
    )
    by = _by_text(lines)
    for raw in ("GIFT CARD 15.00", "BAL: 22.40", "CASH 5.00"):
        assert (by[raw].line_total, "payment_row" in by[raw].flags) == (Decimal("0"), True)
    assert by["FERN BREAD 12.50"].line_total == Decimal("12.50")


def test_a_bought_gift_card_and_a_payment_word_that_is_bought_stay_items():
    lines = _passes(
        ("LANTERN GIFT CARD ACTIVATED 25.00", "item", "25.00"),
        ("CASH BOX STEEL 9.00", "item", "9.00"),  # zeroing it would break the total
        ("CASHEW MIX 4.00", "item", "4.00"),  # not the word "cash"
        total="38.00",
    )
    assert [line.line_total for line in lines] == [
        Decimal("25.00"),
        Decimal("9.00"),
        Decimal("4.00"),
    ]
    assert not any("payment_row" in line.flags for line in lines)


def test_without_a_printed_total_payment_rows_are_left_as_read():
    (line,) = _passes(("GIFT CARD 15.00", "item", "15.00"))
    assert line.line_total == Decimal("15.00") and "payment_row" not in line.flags


# --- 11. A row of an item read as another item -------------------------------


def test_a_multi_buy_rate_row_is_part_of_the_item_above():
    # No printed total needed: 2 x 8.99 is the proof.
    lines = _passes(
        ("Dune Tomatoes Diced", "item", "17.98"),
        ("2 @ 1/ $8.99", "item", "17.98"),
        ("YOU SAVED $2.00", "discount", "2.00"),
    )
    tomatoes, rate, saving = lines
    assert (rate.line_total, "continuation_row" in rate.flags) == (Decimal("0"), True)
    assert (tomatoes.qty, tomatoes.unit_price, tomatoes.line_total) == (
        Decimal("2"),
        Decimal("8.99"),
        Decimal("17.98"),
    )
    assert "qty_from_line_below" in tomatoes.flags
    assert saving.parent_seq == tomatoes.seq  # the saving follows the item, not the rate row


def test_a_for_how_many_rate_comes_to_the_amount():
    lines = _passes(
        ("(SALE) HOLLOW SOY DRINK", "item", "4.47"), ("690245 2 @2/$4.47", "item", "4.47")
    )
    assert lines[1].line_total == Decimal("0") and lines[0].qty == Decimal("2")


def test_a_name_in_another_script_and_a_code_row_fold_when_the_total_agrees():
    lines = _passes(
        ("WILLOW POMELO", "item", "2.68"),
        ("柚子", "item", "2.68"),
        ("FIR SPARKLING WINE", "item", "19.95"),
        ("00004762", "item", "19.95"),
        ("01500ML", "item", "19.95"),
        total="22.63",
    )
    assert [line.line_total for line in lines] == [
        Decimal("2.68"),
        Decimal("0"),
        Decimal("19.95"),
        Decimal("0"),
        Decimal("0"),
    ]
    assert [("continuation_row" in line.flags) for line in lines] == [
        False,
        True,
        False,
        True,
        True,
    ]


@pytest.mark.parametrize(
    ("rows", "total"),
    [
        # Two rows that name a product are two purchases, even at one price.
        ((("1986 TRAIL PACK", "item", "9.97"), ("1986 TRAIL PACK", "item", "9.97")), "19.94"),
        # A rate row that names its product is a purchase of its own.
        ((("COLA 2 @ $1.00", "item", "2.00"), ("COLA 2 @ $1.00", "item", "2.00")), "4.00"),
        # A code row the total needs stays: two produce codes bought twice.
        ((("4011", "item", "1.29"), ("4011", "item", "1.29")), "2.58"),
    ],
)
def test_rows_that_are_purchases_of_their_own_stay(rows, total):
    lines = _passes(*rows, total=total)
    assert all(line.line_total > 0 for line in lines)
    assert not any("continuation_row" in line.flags for line in lines)


def test_without_a_printed_total_only_a_proven_rate_folds():
    lines = _passes(("WILLOW POMELO", "item", "2.68"), ("柚子", "item", "2.68"))
    assert [line.line_total for line in lines] == [Decimal("2.68"), Decimal("2.68")]


def test_a_rate_that_does_not_come_to_the_amount_is_not_a_row_of_it():
    # 2 x 2.50 is not 6.00. (A bare quantity row is the quantity passes' to judge.)
    lines = _passes(("RYE LOAF", "item", "6.00"), ("2 @ $2.50", "item", "6.00"))
    assert not any("continuation_row" in line.flags for line in lines)
