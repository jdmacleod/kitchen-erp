"""OCR text as a soft witness to a reader's amounts (R2-16)."""

from __future__ import annotations

from decimal import Decimal

from app.ingest.witness import any_support, appears_in, numeric_tokens, witness_amounts

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


# --- appears_in: the not_in_scan check (#121, ruling R3) ---------------------


def test_an_amount_appears_with_its_point_its_comma_or_neither():
    for text in ("FIG PRESERVE 7.25", "FIG PRESERVE 7,25", "FIG PRESERVE 725"):
        assert appears_in(D("7.25"), text), text


def test_an_amount_printed_nowhere_does_not_appear():
    assert not appears_in(D("8.15"), "FIG PRESERVE 7.25\nTOTAL 7.25")


def test_an_amount_read_without_its_point_appears_as_printed():
    # The reader kept the bare "612" a till printed for 6.12.
    assert appears_in(D("612"), "PEAR CIDER     612")


def test_letters_ocr_prints_for_digits_count_inside_a_number():
    assert appears_in(D("1.00"), "CARD SAVINGS 1.OO-")
    assert appears_in(D("9.41"), "SMOKED TROUT 9.4l F")
    # Outside a number they are letters: "lOO" alone is not 100.
    assert not appears_in(D("100"), "lOOSE LEAF TEA")


def test_short_amounts_still_count_when_printed():
    # Support needs three digits to rule out chance; appearing does not.
    assert appears_in(D("0.45"), "LEMON .45")


def test_zero_needs_no_print():
    assert appears_in(D("0"), "LOYALTY NOTE")
