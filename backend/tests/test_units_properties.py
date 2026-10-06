"""Property-based guarantees for within-dimension conversion."""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.units import SEED_UNITS, convert_between, units_by_code

UNITS = units_by_code()
BY_DIM = {
    dim: [u.code for u in SEED_UNITS if u.dimension == dim] for dim in ("mass", "volume", "count")
}
TOL = Decimal("1e-9")

quantities = st.decimals(
    min_value=Decimal("0.000001"), max_value=Decimal("1000000"), places=6, allow_nan=False
)
dimensions = st.sampled_from(list(BY_DIM))


def rel_err(a: Decimal, b: Decimal) -> Decimal:
    return abs(a - b) / max(abs(a), abs(b))


@settings(max_examples=400)
@given(quantities, dimensions, st.data())
def test_round_trip_within_dimension(qty, dim, data):
    a = data.draw(st.sampled_from(BY_DIM[dim]))
    b = data.draw(st.sampled_from(BY_DIM[dim]))
    there = convert_between(qty, a, b, UNITS)
    back = convert_between(there, b, a, UNITS)
    assert rel_err(back, qty) <= TOL


@settings(max_examples=400)
@given(quantities, dimensions, st.data())
def test_conversions_compose(qty, dim, data):
    a = data.draw(st.sampled_from(BY_DIM[dim]))
    b = data.draw(st.sampled_from(BY_DIM[dim]))
    c = data.draw(st.sampled_from(BY_DIM[dim]))
    via_b = convert_between(convert_between(qty, a, b, UNITS), b, c, UNITS)
    direct = convert_between(qty, a, c, UNITS)
    assert rel_err(via_b, direct) <= TOL


@given(quantities, dimensions, st.data())
def test_conversion_is_monotone_and_positive(qty, dim, data):
    a = data.draw(st.sampled_from(BY_DIM[dim]))
    b = data.draw(st.sampled_from(BY_DIM[dim]))
    assert convert_between(qty, a, b, UNITS) > 0
    assert convert_between(qty * 2, a, b, UNITS) > convert_between(qty, a, b, UNITS)


@settings(max_examples=300)
@given(
    quantities,
    st.integers(min_value=1, max_value=10000),
    st.sampled_from(BY_DIM["mass"] + BY_DIM["volume"]),
    quantities,
)
def test_a_pack_of_pieces_is_exactly_its_count(packs, count, unit, size):
    """One pack is `count` pieces, exactly, whatever its size or unit; no size leaks in."""
    from app.units import ConversionContext, Pack, ProductContext, convert

    ctx = ConversionContext("each", product=ProductContext(pack=Pack(size, unit, count)))
    result = convert(packs, "each", ctx)
    assert result.qty == packs * count
    assert result.provenance.bridge_kind == "pack_count"
