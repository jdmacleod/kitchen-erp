"""OCR text as a soft witness to a reader's amounts (R2-16)."""

from __future__ import annotations

from decimal import Decimal

from app.ingest.witness import any_support, numeric_tokens, witness_amounts

D = Decimal


def test_tokens_split_at_anything_but_digits_points_and_commas():
    assert numeric_tokens("OAT MILK 2 @ 1.99   3,98 T\nTOTAL 1,204.50") == [
        "2",
        "1.99",
        "3,98",
        "1,204.50",
    ]


def test_an_amount_is_supported_by_an_exact_value():
    text = "PLUM JAM 4.25\nRYE LOAF 3,50"
    assert witness_amounts([D("4.25"), D("3.5"), D("9.99")], text, digit_runs=False) == [
        True,
        True,
        False,
    ]


def test_a_token_supports_one_line_only():
    # Printed once, so it cannot vouch for two lines of the same price.
    assert witness_amounts([D("2.00"), D("2.00")], "TEA 2.00", digit_runs=False) == [True, False]


def test_digit_runs_catch_a_dropped_decimal_point_only_when_asked():
    text = "CHEESE WEDGE 649"  # OCR lost the point in 6.49
    assert witness_amounts([D("6.49")], text, digit_runs=False) == [False]
    assert witness_amounts([D("6.49")], text, digit_runs=True) == [True]
    # Two digits are everywhere on a receipt: never a run.
    assert witness_amounts([D("0.45")], "AISLE 45", digit_runs=True) == [False]


def test_exact_matches_are_assigned_before_digit_runs():
    # 3.49 should take the exact token, leaving 349 for the line that needs it.
    text = "A 349\nB 3.49"
    assert witness_amounts([D("34.90"), D("3.49")], text, digit_runs=True) == [False, True]
    assert witness_amounts([D("3.49"), D("3.49")], text, digit_runs=True) == [True, True]


def test_any_support_checks_one_amount_alone():
    assert any_support(D("12.80"), "SUBTOTAL 12.80", digit_runs=False)
    assert not any_support(D("12.81"), "SUBTOTAL 12.80", digit_runs=True)
