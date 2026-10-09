"""Property: a structured recipe rendered to Cooklang parses back to the same structure (07, 3B).

The renderer lives here, not in the library: the application never writes
recipe files. It renders the canonical spelling of each construct (every
component in brace form, one step per line, blank lines between steps), so a
structure that parses back equal shows the parser reads what it should.
"""

from __future__ import annotations

from decimal import Decimal

import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from app.recipes.cooklang import (
    NO_QUANTITY,
    Cookware,
    IngredientRef,
    Item,
    Quantity,
    QuantityNumber,
    QuantityRange,
    QuantityText,
    Recipe,
    Section,
    Step,
    Text,
    Timer,
    parse,
    parse_number,
)
from app.units.parse import parse_unit

_LETTERS = st.characters(whitelist_categories=("Lu", "Ll", "Nd"), max_codepoint=0x24F)
_WORD = st.text(_LETTERS, min_size=1, max_size=8)


def _phrase(min_words: int = 1, max_words: int = 3) -> st.SearchStrategy[str]:
    return st.lists(_WORD, min_size=min_words, max_size=max_words).map(" ".join)


_LETTERS_ONLY = st.text(
    st.characters(whitelist_categories=("Lu", "Ll"), max_codepoint=0x24F), min_size=1, max_size=8
)
_UNIT = st.one_of(st.sampled_from(["g", "cups", "tbsp", "ml", "cloves", "sprigs"]), _LETTERS_ONLY)
_DECIMAL = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("9999"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_RANGE = st.tuples(_DECIMAL, _DECIMAL).map(lambda pair: QuantityRange(*sorted(pair)))
_QUANTITY: st.SearchStrategy[Quantity] = st.one_of(
    st.just(NO_QUANTITY),
    _DECIMAL.map(QuantityNumber),
    _RANGE,
    st.lists(_LETTERS_ONLY, min_size=1, max_size=2).map(" ".join).map(QuantityText),
)


def _ingredient(name: str, quantity: Quantity, unit: str | None, note: str | None, optional: bool):
    return IngredientRef(
        raw_name=name,
        quantity=quantity,
        unit_text=unit,
        unit=parse_unit(unit) if unit else None,
        note=note,
        optional=optional,
    )


_INGREDIENT = st.builds(
    _ingredient,
    _phrase(),
    _QUANTITY,
    st.one_of(st.none(), _UNIT),
    st.one_of(st.none(), _phrase(1, 4)),
    st.booleans(),
)
_COOKWARE = st.builds(
    Cookware,
    _phrase(),
    st.one_of(st.just(NO_QUANTITY), _DECIMAL.map(QuantityNumber)),
    st.booleans(),
)
_TIMER = st.builds(Timer, st.one_of(st.none(), _WORD), _QUANTITY, st.one_of(st.none(), _UNIT))
_TEXT = st.text(
    st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        whitelist_characters=",.!;'",
        max_codepoint=0x24F,
    ),
    min_size=1,
    max_size=20,
)


@st.composite
def _step(draw: st.DrawFn) -> Step:
    items: list[Item] = []
    for item in draw(
        st.lists(st.one_of(_INGREDIENT, _COOKWARE, _TIMER, _TEXT.map(Text)), min_size=1, max_size=6)
    ):
        if isinstance(item, Text) and items and isinstance(items[-1], Text):
            items[-1] = Text(items[-1].value + item.value)  # the parser merges adjacent text
        else:
            items.append(item)
    if isinstance(items[0], Text):
        items[0] = Text(items[0].value.lstrip())
    if isinstance(items[-1], Text):
        items[-1] = Text(items[-1].value.rstrip())
    items = [item for item in items if not (isinstance(item, Text) and not item.value)]
    if not items or all(isinstance(item, Text) and not item.value.strip() for item in items):
        items = [Text("Stir.")]
    return Step(tuple(items))


_SCALAR_TEXT = st.text(
    st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        whitelist_characters=",.:-!?|'",
        max_codepoint=0x24F,
    ),
    max_size=12,
)
_SCALAR = st.one_of(
    st.none(),
    st.sampled_from(["4", "1.5", "true", "no", "2024-01-02", "", "1|2|3", "2 to 3", "---"]),
    _SCALAR_TEXT,
)
_FRONT_MATTER = st.dictionaries(
    _phrase(),
    st.recursive(
        _SCALAR,
        lambda inner: st.one_of(
            st.lists(inner, max_size=3), st.dictionaries(_phrase(), inner, max_size=3)
        ),
        max_leaves=6,
    ),
    max_size=4,
)


@st.composite
def _recipe(draw: st.DrawFn) -> Recipe:
    front_matter = draw(_FRONT_MATTER)
    sections: list[Section] = []
    if draw(st.booleans()):
        sections.append(Section(None, tuple(draw(st.lists(_step(), min_size=1, max_size=3)))))
    for name in draw(st.lists(_phrase(), max_size=3)):
        sections.append(Section(name, tuple(draw(st.lists(_step(), max_size=3)))))
    servings = front_matter.get("servings")
    servings_text = servings.strip() if isinstance(servings, str) and servings.strip() else None
    title = front_matter.get("title")
    return Recipe(
        front_matter=front_matter,
        sections=tuple(sections),
        title=title if isinstance(title, str) and title.strip() else None,
        servings=parse_number(servings_text) if servings_text else None,
        servings_text=servings_text,
    )


def _render_quantity(quantity: Quantity) -> str:
    if isinstance(quantity, QuantityNumber):
        return str(quantity.value)
    if isinstance(quantity, QuantityRange):
        return f"{quantity.low}-{quantity.high}"
    if isinstance(quantity, QuantityText):
        return quantity.text
    return ""


def _render_body(quantity: Quantity, unit: str | None) -> str:
    body = _render_quantity(quantity)
    if unit:
        body += f"%{unit}"
    return "{" + body + "}"


def render_item(item: Item) -> str:
    if isinstance(item, Text):
        return item.value
    if isinstance(item, IngredientRef):
        text = "@" + ("?" if item.optional else "") + item.raw_name
        text += _render_body(item.quantity, item.unit_text)
        if item.note:
            text += f"({item.note})"
        return text
    if isinstance(item, Cookware):
        return "#" + ("?" if item.optional else "") + item.name + _render_body(item.quantity, None)
    return "~" + (item.name or "") + _render_body(item.quantity, item.unit_text)


def render(recipe: Recipe) -> str:
    out: list[str] = []
    if recipe.front_matter:
        out.append("---")
        out.append(
            yaml.safe_dump(dict(recipe.front_matter), allow_unicode=True, sort_keys=False).rstrip(
                "\n"
            )
        )
        out.append("---")
    for section in recipe.sections:
        if section.name is not None:
            out.append(f"= {section.name}")
        for step in section.steps:
            out.append("".join(render_item(item) for item in step.items))
            out.append("")
    return "\n".join(out) + "\n"


@settings(max_examples=300, deadline=None)
@given(_recipe())
def test_render_then_parse_reproduces_the_structure(recipe: Recipe) -> None:
    text = render(recipe)
    parsed = parse(text)
    assert isinstance(parsed, Recipe), (parsed, text)
    assert parsed == recipe, text


@settings(max_examples=200, deadline=None)
@given(_recipe())
def test_parse_is_idempotent_over_render(recipe: Recipe) -> None:
    first = parse(render(recipe))
    assert isinstance(first, Recipe)
    second = parse(render(first))
    assert second == first
