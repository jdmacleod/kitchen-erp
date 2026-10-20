"""A product's named pieces are a unit of their own: bread sold by the loaf.

A loaf weighs what its label says (10.5 oz for one bread, 16 oz for another), so
"0.5 loaf" reaches grams through the product that names its pieces "loaf", and
measure labels and piece names match their plurals ("2 loaves").
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.units import CanonicalQty, ConversionContext, ConversionFailure, Measure, convert
from app.units.convert import Pack, ProductContext

D = Decimal
OZ = D("28.349523125")
BAGUETTE = ProductContext(piece_name="loaf", piece_pack=Pack(D("10.5"), "oz", 1))
ROLLS = ProductContext(piece_name="roll", piece_pack=Pack(D("12"), "oz", 6))


def ok(result) -> CanonicalQty:
    assert isinstance(result, CanonicalQty), result
    return result


def test_half_a_loaf_is_half_the_labelled_loaf():
    got = ok(convert(D("0.5"), "loaf", ConversionContext("g", product=BAGUETTE)))
    assert got.qty == D("0.5") * D("10.5") * OZ
    assert (got.provenance.bridge_kind, got.provenance.detail) == ("pack", "10.5 oz a loaf")
    assert got.provenance.confirmed is True


def test_a_plural_names_the_same_piece():
    one = ok(convert(D("2"), "loaf", ConversionContext("g", product=BAGUETTE)))
    assert ok(convert(D("2"), "loaves", ConversionContext("g", product=BAGUETTE))).qty == one.qty
    rolls = ok(convert(D("2"), "Rolls", ConversionContext("g", product=ROLLS)))
    assert rolls.qty == D("2") * D("2") * OZ  # 12 oz over 6 rolls is 2 oz a roll
    assert rolls.provenance.detail == "12 oz, 6 pieces"


def test_the_products_pieces_come_before_the_ingredients_measure():
    context = ConversionContext(
        "g", measures=(Measure("loaf", D("450"), "manual", True),), product=BAGUETTE
    )
    assert ok(convert(D("1"), "loaf", context)).provenance.bridge_kind == "pack"


def test_a_measure_matches_its_plural_without_a_product():
    context = ConversionContext("g", measures=(Measure("loaf", D("450"), "manual", True),))
    got = ok(convert(D("2"), "loaves", context))
    assert (got.qty, got.provenance.detail) == (D("900"), "loaf")


def test_another_word_or_no_weight_is_still_unknown():
    for unit, product in [
        ("slice", BAGUETTE),  # not what its pieces are called
        ("loaf", ProductContext(piece_name="loaf", piece_pack=Pack(D("1"), "each", 1))),
        ("loaf", None),
    ]:
        got = convert(D("1"), unit, ConversionContext("g", product=product))
        assert isinstance(got, ConversionFailure) and got.code == "unknown_measure"


def test_a_volume_loaf_needs_a_density():
    soup = ProductContext(piece_name="tub", piece_pack=Pack(D("16"), "fl_oz", 1))
    got = convert(D("1"), "tub", ConversionContext("g", product=soup))
    assert isinstance(got, ConversionFailure) and got.code == "no_density"


@given(
    pieces=st.integers(min_value=1, max_value=24),
    size=st.decimals(min_value=D("0.5"), max_value=D("80"), places=2),
    asked=st.decimals(min_value=D("0.25"), max_value=D("10"), places=2),
)
def test_n_pieces_are_n_parts_of_the_pack(pieces, size, asked):
    product = ProductContext(piece_name="bun", piece_pack=Pack(size, "oz", pieces))
    whole = ok(convert(D(pieces), "buns", ConversionContext("g", product=product))).qty
    part = ok(convert(asked, "bun", ConversionContext("g", product=product))).qty
    assert abs(whole - size * OZ) < D("1e-9")
    assert abs(part - asked * size * OZ / pieces) < D("1e-9")
