"""Table-driven coverage of every branch of the resolution order."""

from decimal import Decimal

import pytest

from app.units import (
    CanonicalQty,
    ConversionContext,
    ConversionFailure,
    Measure,
    Pack,
    ProductContext,
    convert,
)

D = Decimal
GARLIC_CLOVE = Measure("clove", D("5"), "usda", False)
GARLIC_HEAD = Measure("head", D("50"), "measured", True)
FLOUR = ConversionContext("g", density_g_per_ml=D("0.593"), density_source="usda")
FLOUR_CONFIRMED = ConversionContext(
    "g", density_g_per_ml=D("0.593"), density_source="measured", density_confirmed=True
)
GARLIC = ConversionContext("g", measures=(GARLIC_CLOVE, GARLIC_HEAD))
EGGS = ConversionContext("each")
MILK = ConversionContext("ml", density_g_per_ml=D("1.03"), density_source="label")


def ok(result) -> CanonicalQty:
    assert isinstance(result, CanonicalQty), result
    return result


def fail(result, code: str) -> ConversionFailure:
    assert isinstance(result, ConversionFailure), result
    assert result.code == code
    return result


def test_null_qty():
    fail(convert(None, "g", FLOUR), "no_qty")


def test_same_dimension_uses_factor_and_no_bridge():
    r = ok(convert(D("2"), "lb", FLOUR))
    assert r.qty == D("907.18474")
    assert r.unit == "g"
    assert r.provenance.bridge_kind == "none"
    assert r.provenance.rests_on_unconfirmed is False


def test_canonical_each_same_dimension():
    r = ok(convert(D("2"), "dozen", EGGS))
    assert r.qty == D("24")
    assert r.provenance.bridge_kind == "none"


def test_named_measure_takes_precedence_and_carries_provenance():
    r = ok(convert(D("3"), "clove", GARLIC))
    assert r.qty == D("15")
    assert r.provenance.bridge_kind == "measure"
    assert r.provenance.source == "usda"
    assert r.provenance.confirmed is False
    assert r.provenance.detail == "clove"
    assert r.provenance.rests_on_unconfirmed is True
    r2 = ok(convert(D("1"), "Head", GARLIC))  # labels are case-insensitive
    assert r2.qty == D("50")
    assert r2.provenance.confirmed is True


def test_measure_label_shadows_a_unit_code():
    # An ingredient may define a measure whose label equals a unit code; the measure wins.
    ctx = ConversionContext("g", measures=(Measure("cup", D("120"), "measured", True),))
    r = ok(convert(D("2"), "cup", ctx))
    assert r.qty == D("240")
    assert r.provenance.bridge_kind == "measure"


def test_volume_to_mass_uses_ingredient_density():
    r = ok(convert(D("1"), "cup", FLOUR))
    assert r.qty == D("236.5882365") * D("0.593")
    assert r.provenance.bridge_kind == "density"
    assert r.provenance.source == "usda"
    assert r.provenance.confirmed is False


def test_mass_to_volume_divides_by_density():
    r = ok(convert(D("103"), "g", MILK))
    assert r.qty == D("100")
    assert r.provenance.bridge_kind == "density"


def test_product_density_override_beats_ingredient_density():
    ctx = ConversionContext(
        "g",
        density_g_per_ml=D("1.2"),
        density_source="usda",
        product=ProductContext(
            density_g_per_ml=D("0.65"), density_source="measured", density_confirmed=True
        ),
    )
    r = ok(convert(D("100"), "ml", ctx))
    assert r.qty == D("65.00")
    assert r.provenance.bridge_kind == "density_override"
    assert r.provenance.source == "measured"
    assert r.provenance.confirmed is True


def test_no_density_fails_visibly():
    fail(convert(D("1"), "cup", ConversionContext("g")), "no_density")
    fail(convert(D("1"), "g", ConversionContext("ml")), "no_density")


def test_each_with_pack_converts_pack_recursively():
    ctx = ConversionContext("g", product=ProductContext(pack=Pack(D("16"), "oz")))
    r = ok(convert(D("2"), "each", ctx))
    assert r.qty == D("2") * D("16") * D("28.349523125")
    assert r.provenance.bridge_kind == "pack"
    assert r.provenance.detail == "16 oz"
    assert r.provenance.via is None


def test_dozen_with_pack():
    ctx = ConversionContext("ml", product=ProductContext(pack=Pack(D("12"), "fl_oz")))
    r = ok(convert(D("1"), "dozen", ctx))
    assert r.qty == D("12") * D("12") * D("29.5735295625")


def test_pack_crossing_a_density_records_inner_provenance():
    ctx = ConversionContext(
        "g",
        density_g_per_ml=D("0.593"),
        density_source="usda",
        product=ProductContext(pack=Pack(D("2"), "cup")),
    )
    r = ok(convert(D("1"), "each", ctx))
    assert r.provenance.bridge_kind == "pack"
    assert r.provenance.via is not None
    assert r.provenance.via.bridge_kind == "density"
    assert r.provenance.rests_on_unconfirmed is True


def test_pack_without_density_fails_with_no_density():
    ctx = ConversionContext("g", product=ProductContext(pack=Pack(D("2"), "cup")))
    fail(convert(D("1"), "each", ctx), "no_density")


def test_count_with_product_but_no_pack():
    fail(convert(D("1"), "each", ConversionContext("g", product=ProductContext())), "no_pack")


def test_count_with_no_product_and_no_measure():
    fail(convert(D("1"), "each", ConversionContext("g")), "unknown_measure")


def test_unknown_label_is_unknown_measure():
    fail(convert(D("1"), "smidgen", FLOUR), "unknown_measure")


def test_mass_into_count_canonical_has_no_bridge():
    fail(convert(D("500"), "g", EGGS), "unknown_measure")


def test_measure_precedes_pack():
    ctx = ConversionContext(
        "g",
        measures=(Measure("each", D("60"), "manual", True),),
        product=ProductContext(pack=Pack(D("1"), "lb")),
    )
    r = ok(convert(D("1"), "each", ctx))
    assert r.qty == D("60")
    assert r.provenance.bridge_kind == "measure"


def test_invalid_canonical_unit_is_a_programming_error():
    with pytest.raises(ValueError):
        convert(D("1"), "g", ConversionContext("kg"))


def test_results_carry_version():
    assert ok(convert(D("1"), "g", FLOUR)).version == "1"
    assert fail(convert(None, "g", FLOUR), "no_qty").version == "1"
