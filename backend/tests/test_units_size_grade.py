"""A size grade of an ingredient counted in pieces is that many pieces.

USDA weighs "1 large" egg against a medium one, so a "large" measure of 1.136
each made "12 large" eggs 13.6 eggs and two cartons. For an ingredient counted
in pieces the grade says which pieces, not how many; for a weighed ingredient
it is still a measure ("2 large" onions weigh what two large onions weigh).
"""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.units import CanonicalQty, ConversionContext, Measure, convert, units_by_code
from app.units.convert import SIZE_GRADES

D = Decimal
UNITS = units_by_code()
# The shape USDA gives an egg: grades measured against a medium one.
GRADES = (
    Measure("large", D("1.136"), "usda", True),
    Measure("extra large", D("1.273"), "usda", True),
    Measure("small", D("0.864"), "usda", True),
)


def counted(*measures: Measure) -> ConversionContext:
    return ConversionContext(units=UNITS, canonical_unit="each", measures=measures)


def ok(result) -> CanonicalQty:
    assert isinstance(result, CanonicalQty), result
    return result


@pytest.mark.parametrize("grade", ["large", "Large", "extra large", "small", "jumbo", "xl"])
def test_a_grade_of_pieces_is_that_many_pieces(grade):
    got = ok(convert(D("12"), grade, counted(*GRADES)))
    assert (got.qty, got.unit) == (D("12"), "each")
    assert got.provenance.bridge_kind == "none"


def test_a_weighed_ingredient_still_uses_its_grade_measure():
    onion = ConversionContext(
        units=UNITS, canonical_unit="g", measures=(Measure("large", D("150"), "usda", True),)
    )
    got = ok(convert(D("2"), "large", onion))
    assert (got.qty, got.unit, got.provenance.detail) == (D("300"), "g", "large")


def test_other_measures_of_pieces_are_unchanged():
    got = ok(convert(D("1"), "cup", counted(Measure("cup", D("4.86"), "usda", True))))
    assert got.qty == D("4.86")


@given(
    qty=st.decimals(min_value=D("0.001"), max_value=D("10000"), places=3, allow_nan=False),
    grade=st.sampled_from(sorted(SIZE_GRADES)),
)
def test_grading_never_changes_a_count(qty, grade):
    assert ok(convert(qty, grade, counted(*GRADES))).qty == qty
