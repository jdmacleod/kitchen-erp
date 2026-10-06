import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.normalize import NORMALIZE_VERSION
from app.services.normalize import normalize_receipt_text as norm
from tests.normalize_v1 import normalize_v1

TABLE = [
    ("ITAL BOMBA HOT PEP", "ITAL BOMBA HOT PEP"),
    ("  ital  bomba   hot pep ", "ITAL BOMBA HOT PEP"),
    ("ORG BANANAS 4011", "ORG BANANAS 4011"),  # trailing PLU stays: it names the item
    ("4011 ORG BANANAS", "ORG BANANAS"),
    ("0123456789012 KS ALMOND BUTTER", "KS ALMOND BUTTER"),
    ("1234567 KS ALMOND BUTTER 12.99 F", "KS ALMOND BUTTER"),
    ("MILK WHOLE GAL 4.29 T", "MILK WHOLE GAL"),
    ("CHEESE CHEDDAR   3.49 F", "CHEESE CHEDDAR"),
    ("2 @ 1.99 AVOCADO HASS", "AVOCADO HASS"),
    ("2.31 lb @ 3.99/lb HEIRLOOM TOM", "HEIRLOOM TOM"),
    ("HEIRLOOM TOM 2.31 LB @ 3.99 /LB 9.22", "HEIRLOOM TOM"),
    ("SPARKLING WATER CRV 0.10", "SPARKLING WATER CRV"),
    ("COUPON -1.50", "COUPON"),
    ("BEANS BLK 15OZ 0.99 NF", "BEANS BLK 15OZ"),
    ("YOGURT PLAIN 32 OZ *", "YOGURT PLAIN 32 OZ"),
    ("MEMBER SAVINGS $2.00", "MEMBER SAVINGS"),
    ("BOB'S RED MILL OATS", "BOB'S RED MILL OATS"),
    ("HALF & HALF QT", "HALF & HALF QT"),
    ("BREAD--SOURDOUGH", "BREAD--SOURDOUGH"),
    ("- - -", ""),
    # Two price tokens with no separator. The second begins with a digit
    # immediately behind it, which is why the price pattern cannot be anchored
    # with a "not preceded by a digit" lookbehind however tempting that is.
    ("MEMBER SAVINGS $38.64644.58", "MEMBER SAVINGS"),
    ("1.999", "9"),
    # Leftmost match takes "12.34"; ".56" is not a price, so the dot goes and the
    # digits stay. Version 1 did the same — checked against it, not guessed.
    ("12.34.56", "56"),
    # Version 2 (#125): a comma is a decimal point inside a price.
    ("KELP CRISPS 7,25", "KELP CRISPS"),
    ("KELP CRISPS 7.25", "KELP CRISPS"),
    ("KELP CRISPS 7,25 F", "KELP CRISPS"),
    ("COUPON -1,50", "COUPON"),
    ("2,31 lb @ 3,99/lb FIELD TOM", "FIELD TOM"),
    ("2 @ 1,99 QUINCE", "QUINCE"),
    # A comma that is not inside a price is punctuation, as before.
    ("SALT, FLAKED", "SALT FLAKED"),
    ("PACK 6,4", "PACK 6 4"),
]


@pytest.mark.parametrize(("raw", "expected"), TABLE)
def test_table(raw: str, expected: str):
    assert norm(raw) == expected


@pytest.mark.parametrize(("raw", "_"), TABLE)
def test_idempotent(raw: str, _):
    once = norm(raw)
    assert norm(once) == once


def test_version_is_recorded_constant():
    assert NORMALIZE_VERSION == "2"


# The differential test against version 1. Version 2 reads a comma exactly as
# version 1 read a decimal point, and changes nothing else, so for any text:
#   - with no comma, the versions agree;
#   - with commas, version 2 equals version 1 run on the text with each comma
#     made a point.
# Both halves matter: the second alone would allow a change to text with no comma.
_RECEIPT_ALPHABET = st.sampled_from(list("AB TX019.,@$/-*&%'\tLBOZKGEA"))


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
def test_v2_matches_v1_without_a_comma(raw: str):
    raw = raw.replace(",", "")
    assert norm(raw) == normalize_v1(raw)


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
def test_v2_reads_a_comma_as_v1_read_a_point(raw: str):
    assert norm(raw) == normalize_v1(raw.replace(",", "."))


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
def test_v2_is_idempotent(raw: str):
    once = norm(raw)
    assert norm(once) == once


@pytest.mark.parametrize(
    ("raw", "v1"),
    [
        ("KELP CRISPS 7,25", "KELP CRISPS 7 25"),
        ("COUPON -1,50", "COUPON -1 50"),
        ("2,31 lb @ 3,99/lb FIELD TOM", "2 31 LB 3 99/LB FIELD TOM"),
    ],
)
def test_the_intended_differences_from_v1(raw: str, v1: str):
    """What version 1 left in the wording, and version 2 removes."""
    assert normalize_v1(raw) == v1
    assert norm(raw) == norm(raw.replace(",", "."))


# The text reaching this module comes from OCR or from a language model reading a
# photograph, which CLAUDE.md treats as untrusted. A pattern that backtracks is a
# way to spend a worker's CPU on a crafted receipt, so the cost of the worst
# input shape is asserted rather than assumed.
#
# The bound is deliberately loose. It is not a benchmark: it is there to fail if
# someone reintroduces a quantifier that can hand characters back. The version of
# these patterns before the possessive quantifiers took about 3.6 seconds here,
# ten times the current cost, and grew quadratically from there.
def test_long_digit_run_does_not_backtrack():
    import time

    raw = "9" * 8000 + "!"
    started = time.perf_counter()
    normalized = norm(raw)
    elapsed = time.perf_counter() - started

    assert normalized == "9" * 8000
    assert elapsed < 3.0, f"normalize took {elapsed:.2f}s on a digit run; a pattern is backtracking"


@pytest.mark.parametrize(
    "raw",
    [
        "9" * 4000 + "!",
        "ITEM" + " F" * 4000,
        "- " * 4000 + "X",
        " " * 4000 + "@",
        "1." * 4000,
        "$" * 4000 + "1.00",
    ],
)
def test_adversarial_shapes_terminate_quickly(raw: str):
    import time

    started = time.perf_counter()
    norm(raw)
    assert time.perf_counter() - started < 3.0
