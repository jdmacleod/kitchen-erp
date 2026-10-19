"""`|` in an ingredient name: the cooklang-rs alias, and the "this or that" it isn't."""

from app.recipes.cooklang import IngredientRef, Recipe, parse
from app.recipes.cooklang.hints import alias_hints


def ingredients(text: str) -> list[IngredientRef]:
    recipe = parse(text)
    assert isinstance(recipe, Recipe), recipe
    return [ref for s in recipe.sections for step in s.steps for ref in step.ingredients]


def test_an_alias_names_the_ingredient_before_the_pipe():
    [ref] = ingredients("Pour in @lantern cider|cider{1%cup} and stir.")
    assert (ref.raw_name, ref.alias, ref.unit_text) == ("lantern cider", "cider", "cup")
    line = "Pour in @lantern cider|cider{1%cup} and stir."
    assert line[slice(*ref.name_span)] == "lantern cider"
    assert alias_hints(parse(line)) == []


def test_a_name_without_a_pipe_has_no_alias():
    [ref] = ingredients("Add @lantern cider{1%cup}.")
    assert ref.alias is None


def test_a_leading_pipe_stays_as_written_and_gets_a_hint():
    text = "Season with @|lantern cream|fern milk{1/4%cup}."
    [ref] = ingredients(text)
    assert ref.raw_name == "|lantern cream|fern milk"
    [hint] = alias_hints(parse(text))
    assert hint.suggestion == "@lantern cream{…}(or fern milk)"
    assert "display name" in hint.why


def test_a_hint_without_alternatives_suggests_the_name_alone():
    [hint] = alias_hints(parse("Add @|lantern cream{}."))
    assert hint.suggestion == "@lantern cream{}"
