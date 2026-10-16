import re

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from app.services.normalize import NORMALIZE_VERSION
from app.services.normalize import normalize_receipt_text as norm
from tests.normalize_v1 import normalize_v1
from tests.normalize_v2 import normalize_v2
from tests.normalize_v3 import normalize_v3

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
    # Version 3 (#258): clean-up of the ends repeats until nothing more comes off.
    ("0.B-", "0"),  # the dash goes, leaving a flag at the end
    ("CHIPS B -", "CHIPS"),
    ("-1234 MOSS BAY KELP", "MOSS BAY KELP"),  # the dash goes, leaving a code at the start
    ("- 1234 - 5678 KELP T -", "KELP"),
    ("1234", "1234"),  # a lone code is the whole name
    ("T -", "T"),  # and so is a lone flag
    # Version 4 (#399): a point between two digits stays, so a pack size reads.
    ("OAT MILK 1.5L 3.49", "OAT MILK 1.5L"),
    ("BREAD FLOUR STRNG 1.5KG 2.40", "BREAD FLOUR STRNG 1.5KG"),
    ("RVRBND BREAD FLR 2KG 6.50", "RVRBND BREAD FLR 2KG"),
    ("KELP NOODLE 0.5 KG", "KELP NOODLE 0.5 KG"),
    ("GIN 1.75L 29.99", "GIN 1.75L"),  # a volume with two decimals is not a price
    ("SYRUP 0.25ML", "SYRUP 0.25ML"),
    ("FIELD TOM 2.31LB 1.20", "FIELD TOM LB"),  # a weight with two decimals still goes
    ("1..5L", "1 5L"),  # a point next to a point is not between digits
    ("LOOSE .5L", "LOOSE 5L"),
    ("PACK 6,4", "PACK 6 4"),  # a comma outside a price is still punctuation
]


@pytest.mark.parametrize(("raw", "expected"), TABLE)
def test_table(raw: str, expected: str):
    assert norm(raw) == expected


@pytest.mark.parametrize(("raw", "_"), TABLE)
def test_idempotent(raw: str, _):
    once = norm(raw)
    assert norm(once) == once


def test_version_is_recorded_constant():
    assert NORMALIZE_VERSION == "4"


# The differential tests against the frozen versions. Version 2 reads a comma
# exactly as version 1 read a decimal point, and changes nothing else, so for
# any text:
#   - with no comma, the versions agree;
#   - with commas, version 2 equals version 1 run on the text with each comma
#     made a point.
# Both halves matter: the second alone would allow a change to text with no comma.
# Version 3 is version 2 run until the text stops changing, so it agrees with
# version 2 wherever version 2 was already idempotent. Version 4 agrees with
# version 3 on any text with no point or comma between two digits.
_RECEIPT_ALPHABET = st.sampled_from(list("AB TX019.,@$/-*&%'\tLBOZKGEAM"))


def _v2_until_stable(raw: str) -> str:
    text = normalize_v2(raw)
    while (again := normalize_v2(text)) != text:
        text = again
    return text


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
def test_v2_matches_v1_without_a_comma(raw: str):
    raw = raw.replace(",", "")
    assert normalize_v2(raw) == normalize_v1(raw)


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
def test_v2_reads_a_comma_as_v1_read_a_point(raw: str):
    assert normalize_v2(raw) == normalize_v1(raw.replace(",", "."))


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
@example(raw="0.B-")
@example(raw="-1234 KELP")
def test_v3_is_v2_until_stable(raw: str):
    assert normalize_v3(raw) == _v2_until_stable(raw)


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
def test_v3_of_a_v2_key_is_the_v3_key(raw: str):
    """Migration 0049 re-keys stored version 2 keys on their own, without the raw text."""
    assert normalize_v3(normalize_v2(raw)) == normalize_v3(raw)


_BETWEEN_DIGITS = re.compile(r"\d[.,]\d")


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
@example(raw="1.5L")
@example(raw="KELP 1,75L")
def test_v4_matches_v3_without_a_point_between_digits(raw: str):
    if not _BETWEEN_DIGITS.search(raw):
        assert norm(raw) == normalize_v3(raw)


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
@example(raw="0.B-")
@example(raw="1.5.L")
@example(raw="1.-5L")
def test_v4_is_idempotent(raw: str):
    once = norm(raw)
    assert norm(once) == once


@given(st.text(_RECEIPT_ALPHABET, max_size=40))
def test_v4_leaves_a_v3_key_as_it_is(raw: str):
    """Migration 0059 keeps the key of a line with no text: version 4 can't move it."""
    key = normalize_v3(raw)
    assert norm(key) == key


def test_the_naming_pass_reads_the_pack_it_keeps():
    from decimal import Decimal

    from app.services.naming import read_pack

    pack, rest = read_pack(norm("OAT MILK 1.5L 3.49").split())
    assert pack is not None
    assert (pack.qty, rest) == (Decimal("1.5"), ["OAT", "MILK"])


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
    assert normalize_v2(raw) == normalize_v2(raw.replace(",", "."))


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
