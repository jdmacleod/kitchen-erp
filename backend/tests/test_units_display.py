"""Unit prices for display (issue 245): per lb, oz or fl oz by default, kg and L in metric."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.units.display import (
    POUND_G,
    DisplayPrice,
    mass_unit,
    per_unit,
    round_price,
    show,
)

D = Decimal
prices = st.decimals(min_value=D("0.000001"), max_value=D("1000"), places=6)


def test_a_price_per_gram_reads_per_pound():
    # $0.0099/g is $4.49/lb: 0.0099 x 453.59237 = 4.4905...
    assert show(D("0.0099"), "g", "us") == DisplayPrice(D("4.49"), "lb")


def test_small_packs_read_per_ounce():
    assert mass_unit("us", D("85")) == "oz"
    assert mass_unit("us", POUND_G) == "lb"
    assert mass_unit("us", None) == "lb"
    # $0.0882/g is $2.50/oz.
    assert show(D("0.0882"), "g", "us", "oz") == DisplayPrice(D("2.50"), "oz")


def test_volume_reads_per_fluid_ounce_with_three_places_under_a_dollar():
    # $0.0091/ml is $0.269/fl oz; a close neighbour stays distinguishable.
    assert show(D("0.0091"), "ml", "us") == DisplayPrice(D("0.269"), "fl oz")
    assert show(D("0.00925"), "ml", "us") == DisplayPrice(D("0.274"), "fl oz")


def test_each_is_unchanged():
    assert show(D("1.29"), "each", "us") == DisplayPrice(D("1.29"), "each")
    assert show(D("0.5"), "each", "metric") == DisplayPrice(D("0.500"), "each")


def test_metric_reads_per_kilogram_and_litre():
    assert show(D("0.0099"), "g", "metric", "kg") == DisplayPrice(D("9.90"), "kg")
    assert show(D("0.0091"), "ml", "metric") == DisplayPrice(D("9.10"), "L")
    assert mass_unit("metric", D("10")) == "kg"


def test_nothing_to_show_without_a_normalized_price():
    assert show(None, "g", "us") is None
    assert show(D("1"), None, "us") is None
    assert show(D("1"), "furlong", "us") is None


def test_rounding_rule():
    assert round_price(D("4.4905")) == D("4.49")
    assert round_price(D("0.2691")) == D("0.269")
    assert round_price(D("0.9995")) == D("1.000")  # still under $1 before rounding
    assert round_price(D("1.005")) == D("1.01")


def test_a_count_cannot_be_restated_by_weight():
    with pytest.raises(ValueError):
        per_unit(D("1"), "each", "lb")
    with pytest.raises(ValueError):
        per_unit(D("1"), "g", "fl_oz")


@given(prices)
def test_per_pound_restates_per_gram_exactly(p):
    assert per_unit(p, "g", "lb") / POUND_G == p


@given(prices, prices)
def test_order_is_kept(a, b):
    # A cheaper price per gram is never shown dearer per pound (rounding may tie them).
    lo, hi = sorted((a, b))
    assert show(lo, "g", "us").price <= show(hi, "g", "us").price


@given(prices)
def test_precision_follows_the_dollar(p):
    exact = per_unit(p, "ml", "fl_oz")
    places = -show(p, "ml", "us").price.as_tuple().exponent
    assert places == (3 if exact < 1 else 2)
