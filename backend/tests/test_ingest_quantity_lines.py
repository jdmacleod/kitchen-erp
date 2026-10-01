"""A weight or count printed on a row of its own joins its item (#87)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ingest import lines as lines_mod
from app.ingest.schemas import ReceiptLine, ReceiptLines


def _read(*rows: tuple[str, str, str] | tuple[str, str, str, int]) -> list[lines_mod.ParsedLine]:
    """What the model answered, row by row, refined as the stage refines it."""
    return lines_mod.parse_model_lines(
        ReceiptLines(
            lines=[
                ReceiptLine(
                    raw_text=row[0],
                    line_kind=row[1],
                    line_total=Decimal(row[2]),
                    parent_index=row[3] if len(row) > 3 else None,
                )
                for row in rows
            ]
        )
    )


def _shape(lines: list[lines_mod.ParsedLine]) -> list[tuple]:
    return [
        (
            line.seq,
            line.raw_text,
            line.line_kind,
            None if line.qty is None else str(line.qty),
            line.unit,
            None if line.unit_price is None else str(line.unit_price),
            str(line.line_total),
            line.parent_seq,
            line.flags,
        )
        for line in lines
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.71 lb @ 3.99 /lb", ("2.71", "lb", "3.99", None)),
        ("  0.62 kg @ $5.49/kg", ("0.62", "kg", "5.49", None)),
        ("5 @ 0.79", ("5", "each", "0.79", None)),
        ("2.31 lb @ 0.69/lb            1.59", ("2.31", "lb", "0.69", "1.59")),
        ("3 @ 1.25   3.75 F", ("3", "each", "1.25", "3.75")),
    ],
)
def test_a_row_that_is_only_a_quantity_is_read(raw, expected):
    printed = lines_mod.quantity_only(raw)
    assert printed is not None
    assert (
        str(printed.qty),
        printed.unit,
        str(printed.rate),
        None if printed.amount is None else str(printed.amount),
    ) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "AVOCADO HASS  3 @ 0.69  2.07",  # a name: the row is the item
        "BLK BNS 15OZ  2 @ 0.79",
        "2 @ 1.00 SAVE 0.50",
        "2 @ 1.00   2.00-",  # printed negative: a saving
        "2.31 lb @ 0.69/lb  1.59  1.60",  # two amounts: not clear which
        "OAT MILK 3.49",
        "2.31 lb",  # no rate
    ],
)
def test_a_row_with_more_than_a_quantity_is_not_one(raw):
    assert lines_mod.quantity_only(raw) is None


def test_a_weight_above_its_item_joins_it():
    # The model reads both rows as items, each at the full price.
    lines = _read(
        ("2.71 lb @ 3.99 /lb", "item", "10.81"),
        ("WT BROCCOLI CROWNS      10.81 F", "item", "10.81"),
        ("OAT MILK 3.49", "item", "3.49"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == ["2.71 lb @ 3.99 /lb"]
    assert _shape(kept) == [
        (1, "WT BROCCOLI CROWNS      10.81 F", "item", "2.71", "lb", "3.99", "10.81", None,
         ["qty_from_line_above"]),
        (2, "OAT MILK 3.49", "item", "1", "each", None, "3.49", None, []),
    ]  # fmt: skip


def test_a_count_read_as_a_discount_of_the_item_above_joins_the_item_below():
    # The model took "5 @ 0.79" for a saving on the item printed before it.
    lines = _read(
        ("RYE BREAD 4.25", "item", "4.25"),
        ("5 @ 0.79", "discount", "0.79", 0),
        ("AVOCADO                  3.95 F", "item", "3.95"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == ["5 @ 0.79"]
    assert _shape(kept) == [
        (1, "RYE BREAD 4.25", "item", "1", "each", None, "4.25", None, []),
        (2, "AVOCADO                  3.95 F", "item", "5", "each", "0.79", "3.95", None,
         ["qty_from_line_above"]),
    ]  # fmt: skip


def test_the_quantity_rows_own_amount_from_the_model_is_never_used():
    # The model misread the quantity row's amount; the item row's printed amount
    # and the printed rate are what agree.
    lines = _read(
        ("2.71 lb @ 3.99 /lb", "item", "18.01"),
        ("WT BROCCOLI CROWNS 10.81 F", "item", "10.81"),
    )
    kept, _ = lines_mod.merge_quantity_lines(lines)
    assert [(k.raw_text, k.qty, k.line_total) for k in kept] == [
        ("WT BROCCOLI CROWNS 10.81 F", Decimal("2.71"), Decimal("10.81"))
    ]


def test_a_weight_below_a_name_row_joins_it_and_gives_it_the_amount():
    lines = _read(
        ("BANANAS", "item", "0.00"),
        ("2.31 lb @ 0.69/lb            1.59", "item", "1.59"),
        ("SPARKLING WATER 12PK 6.99", "item", "6.99"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == ["2.31 lb @ 0.69/lb            1.59"]
    assert _shape(kept)[0] == (
        1, "BANANAS", "item", "2.31", "lb", "0.69", "1.59", None, ["qty_from_line_below"]
    )  # fmt: skip
    assert len(kept) == 2


def test_a_weight_below_a_priced_item_joins_it():
    lines = _read(
        ("BNLS CHKN BRST       6.84", "item", "6.84"),
        ("   1.96 lb @ 3.49/lb", "item", "6.84"),
        ("TORT FLR 10CT        1.99", "item", "1.99"),
    )
    kept, _ = lines_mod.merge_quantity_lines(lines)
    assert _shape(kept)[0] == (
        1, "BNLS CHKN BRST       6.84", "item", "1.96", "lb", "3.49", "6.84", None,
        ["qty_from_line_below"],
    )  # fmt: skip
    assert len(kept) == 2


def test_a_count_that_fits_both_neighbours_is_left_for_review():
    lines = _read(
        ("AVOCADO 3.95", "item", "3.95"),
        ("5 @ 0.79", "item", "3.95"),
        ("LIMES 3.95", "item", "3.95"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == []
    assert [k.flags for k in kept] == [[], ["qty_inferred", "quantity_line"], []]


def test_a_count_whose_arithmetic_fits_no_neighbour_is_left_for_review():
    lines = _read(
        ("5 @ 0.79", "item", "3.95"),
        ("AVOCADO 3.99", "item", "3.99"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == [] and len(kept) == 2
    assert "quantity_line" in kept[0].flags and kept[1].qty == Decimal("1")


def test_a_quantity_row_never_joins_an_item_that_prints_its_own():
    lines = _read(
        ("2 @ 0.79", "item", "1.58"),
        ("BLK BNS 15OZ  2 @ 0.79   1.58", "item", "1.58"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == [] and "quantity_line" in kept[0].flags


def test_a_name_row_takes_only_an_amount_the_arithmetic_supports():
    lines = _read(
        ("BANANAS", "item", "1.59"),
        ("2.31 lb @ 0.69/lb   9.99", "item", "9.99"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == []
    assert kept[0].line_total == Decimal("1.59") and "quantity_line" in kept[1].flags


def test_an_item_takes_one_quantity_row_only():
    # Two weight rows around one item: the first joins it, the second stays.
    lines = _read(
        ("1.00 lb @ 2.00/lb", "item", "2.00"),
        ("PEARS 2.00", "item", "2.00"),
        ("1.00 lb @ 2.00/lb", "item", "2.00"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == ["1.00 lb @ 2.00/lb"]
    assert [k.flags for k in kept] == [["qty_from_line_above"], ["qty_inferred", "quantity_line"]]


def test_the_issue_pairs_reconcile_with_the_printed_total():
    lines = _read(
        ("2.71 lb @ 3.99 /lb", "item", "10.81"),
        ("WT BROCCOLI CROWNS      10.81 F", "item", "10.81"),
        ("5 @ 0.79", "discount", "3.95", 1),
        ("AVOCADO                  3.95 F", "item", "3.95"),
        ("MEMBER SAVINGS 0.50-", "discount", "0.50", 3),
    )
    printed_total = Decimal("14.26")
    assert lines_mod.reconcile(lines, printed_total, None)["mismatch"]
    kept, _ = lines_mod.merge_quantity_lines(lines)
    assert not lines_mod.reconcile(kept, printed_total, None)["mismatch"]
    # The saving printed beneath the avocados still belongs to them.
    assert [(k.raw_text, k.parent_seq) for k in kept][-1] == ("MEMBER SAVINGS 0.50-", 2)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # OCR reads "lb" as "Ib", "1b" or "|b", and some tills print a rate with
        # no leading zero; found reading real receipts after #87.
        ("2.22 Ib @ $ 4.19/ Ib", ("2.22", "lb", "4.19")),
        ("3.26 1b @ $ 4.19/ Ib", ("3.26", "lb", "4.19")),
        ("2.84 lb @$ .89 / |b", ("2.84", "lb", "0.89")),
    ],
)
def test_ocr_spellings_of_a_weight_row_are_read(raw, expected):
    printed = lines_mod.quantity_only(raw)
    assert printed is not None
    assert (str(printed.qty), printed.unit, str(printed.rate)) == expected


def test_a_tax_letter_read_as_a_digit_is_cut_back_to_the_cents():
    # "4.49 S" OCR'd as "4.498"; the model rounds it to 4.50.
    [line] = _read(("GOAT CHEESE LOG 4.498", "item", "4.50"))
    assert line.line_total == Decimal("4.49") and "tax_code_as_digit" in line.flags
    # A reading that doesn't match the three-decimal amount is left alone.
    [line] = _read(("GOAT CHEESE LOG 4.498", "item", "9.99"))
    assert line.line_total == Decimal("9.99") and "tax_code_as_digit" not in line.flags
    [line] = _read(("GOAT CHEESE LOG 4.49 S", "item", "4.49"))
    assert line.flags == []


def test_an_ocr_weight_row_joins_an_item_whose_tax_letter_was_read_as_a_digit():
    lines = _read(
        ("0.42 Ib @ $2.99 /Ib", "item", "0.42"),
        ("FRESH GINGER 1.268", "item", "1.27"),
        ("OAT MILK 3.49 F", "item", "3.49"),
    )
    kept, merged = lines_mod.merge_quantity_lines(lines)
    assert merged == ["0.42 Ib @ $2.99 /Ib"]
    ginger = kept[0]
    assert (ginger.qty, ginger.unit, ginger.unit_price, ginger.line_total) == (
        Decimal("0.42"),
        "lb",
        Decimal("2.99"),
        Decimal("1.26"),
    )
    assert not lines_mod.reconcile(kept, Decimal("4.75"), None)["mismatch"]
