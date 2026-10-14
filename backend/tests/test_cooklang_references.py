"""`@&` references to an earlier ingredient, step or section (issue 367).

The cooklang-rs extensions: `@&name` is an ingredient named earlier, and
`@&(~1)`, `@&(2)`, `@&(=2)` and `@&(=~1)` are an earlier step or section used as
an ingredient. A reference without an amount is never an ingredient line; one
with an amount (`@&flour{50%g}`) is, so that it adds to the first.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.recipes.cooklang import (
    IngredientRef,
    ParseError,
    QuantityNumber,
    Recipe,
    Reference,
    parse,
)
from app.recipes.cooklang.hints import reference_hints

FLATBREAD = """\
= Dough
Mix @flour{200%g} and @water{120%ml}.

Knead the @&(~1)dough{} for ~{5%minutes}, adding @&flour{20%g} if it sticks.

= Topping
Stir @olive oil{2%tbsp} with @za'atar{1%tbsp}.

= Baking
Roll out the @&(=1)dough{1%ball}(rested), spread the @&(=~1)topping{}, and dust with @&flour.

Bake on a #tray{} and serve with the @&(1)dough{}(any scraps, baked).
"""


def _recipe(text: str) -> Recipe:
    parsed = parse(text)
    assert isinstance(parsed, Recipe), parsed
    return parsed


def _references(recipe: Recipe) -> list[Reference]:
    return [
        item
        for section in recipe.sections
        for step in section.steps
        for item in step.items
        if isinstance(item, Reference)
    ]


def test_each_form_points_at_where_it_came_from() -> None:
    refs = _references(_recipe(FLATBREAD))
    assert [(r.name, r.target, r.section, r.step) for r in refs] == [
        ("dough", "step", 0, 0),  # (~1): the step before, in this section
        ("dough", "section", 0, None),  # (=1): section 1
        ("topping", "section", 1, None),  # (=~1): the section before
        ("flour", "ingredient", 0, 0),  # @&flour: where flour was first named
        ("dough", "step", 2, 0),  # (1): step 1 of this section
    ]
    rested = refs[1]
    assert rested.quantity == QuantityNumber(Decimal(1))
    assert (rested.unit_text, rested.note) == ("ball", "rested")


def test_a_reference_is_not_an_ingredient_line_but_an_added_amount_is() -> None:
    recipe = _recipe(FLATBREAD)
    lines = [(ref.raw_name, ref.reference) for ref in recipe.ingredients]
    assert lines == [
        ("flour", False),
        ("water", False),
        ("flour", True),  # @&flour{20%g}: 20 g more, a line of its own
        ("olive oil", False),
        ("za'atar", False),
    ]
    assert "dough" not in {name for _, name in recipe.ingredient_lines}


def test_an_ingredient_reference_matches_the_name_whatever_its_case_or_spacing() -> None:
    recipe = _recipe("Add @Rice  Noodles{1%lb}.\n\nToss in the @&rice noodles{}.")
    [ref] = _references(recipe)
    assert (ref.target, ref.section, ref.step) == ("ingredient", 0, 0)


def test_without_the_ampersand_nothing_changes() -> None:
    recipe = _recipe("Add @flour{1%cup}.\n\nAdd @flour{1%cup}.")
    assert [type(i) for i in recipe.ingredients] == [IngredientRef, IngredientRef]
    assert _references(recipe) == []


@pytest.mark.parametrize(
    ("text", "message", "line", "column"),
    [
        ("Add @&salt{}.", "`@&salt` must name an earlier ingredient", 1, 5),
        ("Add @&salt{1%tsp}.", "`@&salt` must name an earlier ingredient", 1, 5),
        ("Mix @&(~1)dough{}.", "`@&(~1)dough` must name an earlier step of this section", 1, 5),
        # Step references stay within their section, as cooklang-rs has them.
        (
            "= A\nMix @flour.\n\n= B\nRest the @&(1)dough.",
            "`@&(1)dough` must name an earlier step of this section",
            5,
            10,
        ),
        (
            "Mix @flour.\n\nUse @&(2)dough.",
            "`@&(2)dough` must name an earlier step of this section",
            3,
            5,
        ),
        ("= A\nx\n\n= B\nUse @&(=2)sauce{}.", "`@&(=2)sauce` must name an earlier section", 5, 5),
        ("= A\nUse @&(=~1)sauce{}.", "`@&(=~1)sauce` must name an earlier section", 2, 5),
    ],
)
def test_a_reference_to_nothing_earlier_is_an_error_at_the_reference(
    text: str, message: str, line: int, column: int
) -> None:
    assert parse(text) == ParseError(message, line, column)


@pytest.mark.parametrize("spec", ["(x)", "(=0)", "(~)", "(=~-1)", "(2", "()"])
def test_a_target_that_is_not_a_step_or_section_number_is_an_error(spec: str) -> None:
    parsed = parse(f"Mix @flour.\n\nUse @&{spec}dough{{}}.")
    assert isinstance(parsed, ParseError)
    assert (parsed.line, parsed.column) == (3, 7)
    assert "~N (steps back), N (step N), =N (section N) or =~N (sections back)" in parsed.message


def test_the_reference_records_its_token_span() -> None:
    [ref] = _references(_recipe("Mix @flour.\n\nUse the @&(~1)dough{} now."))
    assert ref.span == (8, 21)  # "@&(~1)dough{}"


# --- kerp recipes lint's hints ----------------------------------------------------------------


def test_hints_name_a_section_or_a_repeat_in_a_measure_that_does_not_convert() -> None:
    recipe = _recipe(
        "= Prep\nSoak @rice noodles{1%lb} and slice @garlic{4%cloves}.\n\n"
        "= Glaze\nWhisk @tamari{2%tbsp} with @honey{1%tbsp}.\n\n"
        "= Cook\nFry @garlic{2%cloves}, then add @rice noodles{1%portion}, "
        "@glaze{1%portion} and @&rice noodles{}."
    )
    hints = [(h.line, h.written, h.suggestion, h.why) for h in reference_hints(recipe)]
    assert hints == [
        (8, "rice noodles", "@&rice noodles{}", "repeats the ingredient from line 2"),
        (8, "glaze", "@&(=2)glaze{…}", "names section 2"),
    ]
    # More garlic in the same measure is an amount added, not a reference.
