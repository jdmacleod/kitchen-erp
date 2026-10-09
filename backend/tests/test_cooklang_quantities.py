"""Every quantity form parses exactly, in Decimal; notes and anonymous timers parse (07, 3B, 11)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.recipes.cooklang import (
    NO_QUANTITY,
    IngredientRef,
    QuantityNumber,
    QuantityRange,
    QuantityText,
    Recipe,
    Timer,
    parse,
    parse_quantity,
)
from app.units.parse import UnitParseFailure

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "recipes"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3", Decimal("3")),
        ("250", Decimal("250")),
        ("1.5", Decimal("1.5")),
        ("0.25", Decimal("0.25")),
        ("1/2", Decimal("0.5")),
        ("1 / 2", Decimal("0.5")),
        ("3/4", Decimal("0.75")),
        ("1 1/2", Decimal("1.5")),
        ("2 3/4", Decimal("2.75")),
        ("¾", Decimal("0.75")),
        ("½", Decimal("0.5")),
        ("2½", Decimal("2.5")),
        ("2 ½", Decimal("2.5")),
        ("1⅛", Decimal("1.125")),
    ],
)
def test_number_forms_are_exact_decimals(text: str, expected: Decimal) -> None:
    quantity = parse_quantity(text)
    assert isinstance(quantity, QuantityNumber)
    assert quantity.value == expected
    assert isinstance(quantity.value, Decimal)
    assert quantity.kind == "number"


def test_non_terminating_fraction_is_a_28_digit_decimal() -> None:
    quantity = parse_quantity("1/3")
    assert isinstance(quantity, QuantityNumber)
    assert quantity.value == Decimal("0.3333333333333333333333333333")
    third = parse_quantity("⅓")
    assert isinstance(third, QuantityNumber)
    assert third.value == quantity.value


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("2-3", Decimal("2"), Decimal("3")),
        ("2 - 3", Decimal("2"), Decimal("3")),
        ("2–3", Decimal("2"), Decimal("3")),
        ("2 to 3", Decimal("2"), Decimal("3")),
        ("1/2-3/4", Decimal("0.5"), Decimal("0.75")),
        ("1 1/2 to 2", Decimal("1.5"), Decimal("2")),
        ("½-1", Decimal("0.5"), Decimal("1")),
        ("8 to 10", Decimal("8"), Decimal("10")),
    ],
)
def test_range_forms(text: str, low: Decimal, high: Decimal) -> None:
    quantity = parse_quantity(text)
    assert isinstance(quantity, QuantityRange)
    assert (quantity.low, quantity.high) == (low, high)
    assert quantity.kind == "range"


@pytest.mark.parametrize(
    "text",
    ["some", "a handful", "few", "7 k", "01/2", "three", "to taste", "1-2-3", "3 to taste", "1/0"],
)
def test_text_forms_stay_text(text: str) -> None:
    quantity = parse_quantity(text)
    assert isinstance(quantity, QuantityText)
    assert quantity.text == text
    assert quantity.kind == "text"


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_is_none(text: str) -> None:
    assert parse_quantity(text) is NO_QUANTITY
    assert NO_QUANTITY.kind == "none"


def _only_ingredient(source: str) -> IngredientRef:
    recipe = parse(source)
    assert isinstance(recipe, Recipe), recipe
    (ingredient,) = recipe.ingredients
    return ingredient


def test_ingredient_in_a_step_carries_quantity_unit_and_note() -> None:
    ingredient = _only_ingredient("Fry @red onion{1 1/2%cups}(finely sliced) gently.")
    assert ingredient.raw_name == "red onion"
    assert ingredient.quantity == QuantityNumber(Decimal("1.5"))
    assert ingredient.unit_text == "cups"
    assert ingredient.unit == "cup"
    assert ingredient.note == "finely sliced"
    assert ingredient.line == 1


def test_measure_label_stays_text_with_a_typed_failure() -> None:
    ingredient = _only_ingredient("Add @garlic{3%cloves}.")
    assert ingredient.unit_text == "cloves"
    assert ingredient.unit == UnitParseFailure("cloves")


def test_unicode_and_mixed_quantities_inside_a_recipe() -> None:
    recipe = parse("Mix @flour{2½%cups} with @sugar{¾%cup} and @eggs{2-3}.")
    assert isinstance(recipe, Recipe)
    flour, sugar, eggs = recipe.ingredients
    assert flour.quantity == QuantityNumber(Decimal("2.5"))
    assert sugar.quantity == QuantityNumber(Decimal("0.75"))
    assert eggs.quantity == QuantityRange(Decimal("2"), Decimal("3"))
    assert eggs.unit_text is None and eggs.unit is None


def test_text_quantity_in_a_recipe() -> None:
    ingredient = _only_ingredient("Season with @salt{some}.")
    assert ingredient.quantity == QuantityText("some")


def test_anonymous_and_named_timers() -> None:
    recipe = parse("Simmer for ~{3%minutes}, then rest ~rest{10%minutes} and ~stand.")
    assert isinstance(recipe, Recipe)
    anonymous, named, bare = recipe.timers
    assert anonymous == Timer(None, QuantityNumber(Decimal("3")), "minutes")
    assert named == Timer("rest", QuantityNumber(Decimal("10")), "minutes")
    assert bare == Timer("stand", NO_QUANTITY, None)


def test_servings_number_and_text() -> None:
    four = parse("---\nservings: 4\n---\n")
    assert isinstance(four, Recipe)
    assert four.servings == Decimal("4") and four.servings_text == "4"
    assert isinstance(four.servings, Decimal)
    ranged = parse("---\nservings: 2 to 3\n---\n")
    assert isinstance(ranged, Recipe)
    assert ranged.servings is None and ranged.servings_text == "2 to 3"
    none = parse("Just a step.")
    assert isinstance(none, Recipe)
    assert none.servings is None and none.servings_text is None


def test_front_matter_scalars_never_become_floats_or_ints() -> None:
    recipe = parse("---\nservings: 4\nyield: 1.5\nflag: true\nwhen: 2024-01-02\nempty:\n---\n")
    assert isinstance(recipe, Recipe)
    assert recipe.front_matter == {
        "servings": "4",
        "yield": "1.5",
        "flag": "true",
        "when": "2024-01-02",
        "empty": None,
    }


def test_nested_front_matter_is_kept_verbatim() -> None:
    source = "---\ntitle: Pond lily stew\noven:\n  temperature: 180 C\n  rack: middle\n"
    recipe = parse(source + "tags: [a, b]\n---\n")
    assert isinstance(recipe, Recipe)
    assert recipe.title == "Pond lily stew"
    assert recipe.front_matter["oven"] == {"temperature": "180 C", "rack": "middle"}
    assert recipe.front_matter["tags"] == ["a", "b"]


def test_legacy_metadata_lines_join_the_front_matter() -> None:
    recipe = parse("---\ntitle: Early\n---\n>> servings: 6\n>> source: invented\nStir.")
    assert isinstance(recipe, Recipe)
    assert recipe.front_matter == {"title": "Early", "servings": "6", "source": "invented"}
    assert recipe.servings == Decimal("6")


def test_sections_steps_and_order() -> None:
    recipe = parse("= Base\nAdd @a{1}.\n\nAdd @b{2}.\n== Top ==\nAdd @c{3} and #pan{}.\n")
    assert isinstance(recipe, Recipe)
    assert [section.name for section in recipe.sections] == ["Base", "Top"]
    assert [len(section.steps) for section in recipe.sections] == [2, 1]
    assert [i.raw_name for i in recipe.ingredients] == ["a", "b", "c"]
    assert recipe.ingredient_lines[2][0] == "Top"
    assert [c.name for c in recipe.cookware] == ["pan"]


def test_comments_vanish_and_blank_lines_split_steps() -> None:
    recipe = parse("Add @a{} -- not this\n[- nor\nthis -] then @b{}.\n\nNext.")
    assert isinstance(recipe, Recipe)
    first, second = recipe.steps
    assert [i.raw_name for i in first.ingredients] == ["a", "b"]
    assert second.items[0].value == "Next."  # type: ignore[union-attr]


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.cook")), ids=lambda p: p.stem)
def test_fixture_recipes_parse(path: Path) -> None:
    recipe = parse(path.read_text(encoding="utf-8"))
    assert isinstance(recipe, Recipe), recipe
    assert recipe.ingredients
    for ingredient in recipe.ingredients:
        if isinstance(ingredient.quantity, QuantityNumber):
            assert isinstance(ingredient.quantity.value, Decimal)


def test_fixture_corpus_covers_every_quantity_kind() -> None:
    kinds: set[str] = set()
    notes = timers = 0
    for path in FIXTURES.glob("*.cook"):
        recipe = parse(path.read_text(encoding="utf-8"))
        assert isinstance(recipe, Recipe)
        kinds.update(i.quantity.kind for i in recipe.ingredients)
        notes += sum(1 for i in recipe.ingredients if i.note)
        timers += sum(1 for t in recipe.timers if t.name is None)
    assert kinds == {"number", "range", "text", "none"}
    assert notes and timers
