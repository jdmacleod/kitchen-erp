"""Qualified measure labels and bare counts (CONVERT_VERSION 6).

USDA's portions name measures with a qualifier ("tbsp, whole"). A recipe asks
for "tbsp", so the one measure whose label before the comma is the unit stands
for it; two candidates mean neither. A bare count of an ingredient weighed in
grams ("2 onions") is that many of its piece measure, as a pack in pieces is.
"""

from decimal import Decimal

from app.units import CanonicalQty, ConversionContext, ConversionFailure, Measure, convert

D = Decimal
FENNEL = ConversionContext(
    "g",
    measures=(
        Measure("tbsp, whole", D("5.8"), "usda", True),
        Measure("tsp, whole", D("2"), "usda", True),
    ),
)


def ok(result) -> CanonicalQty:
    assert isinstance(result, CanonicalQty), result
    return result


def test_the_only_qualified_measure_stands_for_its_unit():
    r = ok(convert(D("2"), "tbsp", FENNEL))
    assert r.qty == D("11.6")
    assert r.provenance.bridge_kind == "measure"
    assert r.provenance.detail == "tbsp, whole"


def test_an_exact_label_beats_a_qualified_one():
    ctx = ConversionContext(
        "g",
        measures=(
            Measure("cup, chopped", D("160"), "usda", True),
            Measure("cup", D("150"), "x", True),
        ),
    )
    assert ok(convert(D("1"), "cup", ctx)).qty == D("150")


def test_two_qualified_measures_are_neither_and_the_failure_names_them():
    ctx = ConversionContext(
        "g",
        measures=(
            Measure("cup, chopped", D("160"), "usda", True),
            Measure("cup, sliced", D("115"), "usda", True),
        ),
    )
    r = convert(D("1"), "cup", ctx)
    assert isinstance(r, ConversionFailure)
    assert r.code == "no_density"
    assert "'cup, chopped' and 'cup, sliced' both fit 'cup'" in r.message


def test_two_qualified_measures_fall_through_to_a_density():
    ctx = ConversionContext(
        "g",
        density_g_per_ml=D("0.5"),
        density_source="usda",
        measures=(
            Measure("cup, chopped", D("160"), "usda", True),
            Measure("cup, sliced", D("115"), "usda", True),
        ),
    )
    r = ok(convert(D("2"), "cup", ctx))
    assert r.qty == ok(convert(D("1"), "cup", ConversionContext("ml"))).qty
    assert r.provenance.bridge_kind == "density"


def test_a_qualifier_is_not_a_prefix_match():
    ctx = ConversionContext("g", measures=(Measure("tbsp whole", D("5.8"), "usda", True),))
    assert isinstance(convert(D("1"), "tbsp", ctx), ConversionFailure)


def test_a_bare_count_of_a_weighed_ingredient_is_its_piece_measure():
    onion = ConversionContext("g", measures=(Measure("medium", D("110"), "usda", False),))
    r = ok(convert(D("2"), "each", onion))
    assert r.qty == D("220")
    assert r.provenance.detail == "medium"
    assert r.provenance.rests_on_unconfirmed


def test_each_prefers_an_each_measure_then_whole_then_medium():
    ctx = ConversionContext(
        "g",
        measures=(
            Measure("medium", D("110"), "usda", True),
            Measure("whole", D("120"), "usda", True),
        ),
    )
    assert ok(convert(D("1"), "each", ctx)).qty == D("120")


def test_a_counted_ingredient_keeps_counting_pieces():
    eggs = ConversionContext("each", measures=(Measure("medium", D("0.9"), "usda", True),))
    r = ok(convert(D("2"), "each", eggs))
    assert r.qty == D("2")
    assert r.provenance.bridge_kind == "none"
