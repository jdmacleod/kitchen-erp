"""Bunches (issue 371): a family of their own, weighed only through a "bunch" measure."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.packs import pack_canonical_qty
from app.units import (
    CanonicalQty,
    ConversionContext,
    ConversionFailure,
    Measure,
    Pack,
    ProductContext,
    convert,
    convert_between,
    parse_unit,
    units_by_code,
)
from app.units.display import show

D = Decimal
UNITS = units_by_code()
BUNCH = Measure("bunch", D("90"), "measured", True)
EACH = Measure("each", D("12"), "measured", True)

quantities = st.decimals(
    min_value=Decimal("0.001"), max_value=Decimal("10000"), places=3, allow_nan=False
)
counts = st.sampled_from(["each", "dozen"])


def ok(result) -> CanonicalQty:
    assert isinstance(result, CanonicalQty), result
    return result


def fail(result) -> ConversionFailure:
    assert isinstance(result, ConversionFailure), result
    assert result.code == "no_bunch_weight"
    return result


class _Product:
    def __init__(self, qty: str, unit: str):
        self.pack_qty, self.pack_unit, self.pack_count = D(qty), unit, None


def test_bunch_is_its_own_family():
    bunch = UNITS["bunch"]
    assert bunch.dimension == "bunch" and bunch.to_base_factor == 1
    assert parse_unit("bunches") == "bunch" and parse_unit("Bunch") == "bunch"
    with pytest.raises(ValueError, match="differ in dimension"):
        convert_between(D("1"), "bunch", "each", UNITS)


def test_a_bunch_without_a_measure_is_a_typed_failure_with_its_count():
    r = fail(convert(D("2"), "bunch", ConversionContext("g")))
    assert r.bunches == D("2")


def test_a_bunch_is_never_a_piece():
    """An "each" measure weighs a piece; it never weighs a bunch."""
    ctx = ConversionContext("g", measures=(EACH,))
    fail(convert(D("1"), "bunch", ctx))
    packed = ConversionContext("g", measures=(EACH,), product=ProductContext(Pack(D("1"), "bunch")))
    fail(convert(D("1"), "each", packed))


def test_a_bunch_is_weighed_through_the_bunch_measure():
    r = ok(convert(D("2"), "bunch", ConversionContext("g", measures=(BUNCH,))))
    assert r.qty == D("180")
    assert r.provenance.bridge_kind == "measure" and r.provenance.detail == "bunch"


def test_a_pack_of_one_bunch_is_priced_through_the_measure():
    ctx = ConversionContext("g", measures=(BUNCH,), product=ProductContext(Pack(D("1"), "bunch")))
    r = ok(convert(D("3"), "each", ctx))
    assert r.qty == D("270")
    assert r.provenance.bridge_kind == "pack" and r.provenance.detail == "1 bunch"
    assert r.provenance.via is not None and r.provenance.via.bridge_kind == "measure"


def test_a_pack_of_bunches_without_a_measure_counts_its_bunches():
    ctx = ConversionContext("g", product=ProductContext(Pack(D("2"), "bunch")))
    assert fail(convert(D("3"), "each", ctx)).bunches == D("6")


def test_a_bunch_in_a_count_ingredient_needs_a_measure_too():
    fail(convert(D("1"), "bunch", ConversionContext("each")))
    ctx = ConversionContext("each", measures=(Measure("bunch", D("6"), "manual", False),))
    r = ok(convert(D("1"), "bunch", ctx))
    assert r.qty == D("6") and r.provenance.rests_on_unconfirmed


def test_a_bunch_ignores_a_pack_in_another_unit():
    """A bag's pack is not a bunch: "1 bunch" of it is still one bunch."""
    ctx = ConversionContext("g", product=ProductContext(Pack(D("500"), "g")))
    assert fail(convert(D("1"), "bunch", ctx)).bunches == D("1")


def test_a_bunch_is_never_the_canonical_unit():
    with pytest.raises(ValueError):  # noqa: PT011
        convert(D("1"), "bunch", ConversionContext("bunch"))


def test_bunch_pack_canonical_quantity():
    ctx = ConversionContext("g", measures=(BUNCH, EACH))
    assert pack_canonical_qty(_Product("1", "bunch"), ctx) == D("90")
    assert pack_canonical_qty(_Product("1", "bunch"), ConversionContext("g")) is None


def test_per_bunch_prices_read_a_bunch():
    shown = show(D("1.29"), "bunch", "us")
    assert shown is not None and shown.price == D("1.29") and shown.unit == "bunch"
    assert show(D("0.5"), "bunch", "metric").unit == "bunch"


@given(quantities, quantities)
def test_bunches_weigh_exactly_their_measure(n, weight):
    ctx = ConversionContext("g", measures=(Measure("bunch", weight, "manual", True),))
    assert ok(convert(n, "bunch", ctx)).qty == n * weight


@given(quantities, quantities, quantities)
def test_packs_of_bunches_weigh_exactly(packs, per_pack, weight):
    ctx = ConversionContext(
        "g",
        measures=(Measure("bunch", weight, "manual", True),),
        product=ProductContext(Pack(per_pack, "bunch")),
    )
    assert ok(convert(packs, "each", ctx)).qty == packs * per_pack * weight


@given(quantities, quantities, counts, st.sampled_from(["g", "ml", "each"]))
def test_no_piece_measure_ever_weighs_a_bunch(n, weight, piece, canonical):
    """Whatever piece measures the ingredient has, a bunch without a bunch measure fails."""
    measures = tuple(Measure(label, weight, "manual", True) for label in ("each", "whole", piece))
    result = convert(n, "bunch", ConversionContext(canonical, measures=measures))
    assert fail(result).bunches == n
    packed = ConversionContext(
        canonical, measures=measures, product=ProductContext(Pack(n, "bunch"))
    )
    assert fail(convert(D("1"), "each", packed)).bunches == n


@given(quantities, counts)
def test_a_count_is_never_a_bunch(n, unit):
    """Pieces never become bunches: a count canonical takes "each" and "dozen" as they are."""
    r = ok(convert(n, unit, ConversionContext("each", measures=(BUNCH,))))
    assert r.qty == n * UNITS[unit].to_base_factor
    assert r.provenance.bridge_kind == "none"
