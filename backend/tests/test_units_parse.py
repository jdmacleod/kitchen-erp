import pytest

from app.units import UnitParseFailure, parse_unit

TABLE = [
    ("lbs", "lb"),
    ("lb", "lb"),
    ("Pound", "lb"),
    ("POUNDS", "lb"),
    ("oz", "oz"),
    ("ounces", "oz"),
    ("Tbsp", "tbsp"),
    ("TBSP", "tbsp"),
    ("tablespoon", "tbsp"),
    ("tbs", "tbsp"),
    ("T", "tbsp"),
    ("t", "tsp"),
    ("tsp", "tsp"),
    ("teaspoons", "tsp"),
    ("fluid ounces", "fl_oz"),
    ("fl oz", "fl_oz"),
    ("fl. oz.", "fl_oz"),
    ("fl.oz", "fl_oz"),
    ("FL OZ", "fl_oz"),
    ("cup", "cup"),
    ("Cups", "cup"),
    ("c", "cup"),
    ("C", "cup"),
    ("pint", "pt"),
    ("qt", "qt"),
    ("gallons", "gal"),
    ("g", "g"),
    ("grams", "g"),
    ("kg", "kg"),
    ("kilos", "kg"),
    ("mg", "mg"),
    ("ml", "ml"),
    ("millilitres", "ml"),
    ("cc", "ml"),
    ("L", "l"),
    ("liters", "l"),
    ("each", "each"),
    ("ea", "each"),
    ("pcs", "each"),
    ("ct", "each"),
    ("dozen", "dozen"),
    ("doz", "dozen"),
    ("  Lbs  ", "lb"),
    ("fl_oz", "fl_oz"),
]


@pytest.mark.parametrize(("text", "code"), TABLE)
def test_alias_table(text: str, code: str):
    assert parse_unit(text) == code


def test_case_sensitive_t_distinction():
    assert parse_unit("T") == "tbsp"
    assert parse_unit("t") == "tsp"
    # Only the bare letters are case-sensitive.
    assert parse_unit("TSP") == "tsp"
    assert parse_unit("tbsp") == "tbsp"


@pytest.mark.parametrize("text", ["", "   ", "smidgen", "handful", "12", "kgg", "cups of"])
def test_unknown_text_is_typed_failure(text: str):
    result = parse_unit(text)
    assert isinstance(result, UnitParseFailure)
    assert result.code == "unknown_unit"


def test_non_string_does_not_raise():
    assert isinstance(parse_unit(None), UnitParseFailure)  # type: ignore[arg-type]
