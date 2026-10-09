"""The Cooklang canonical suite, vendored under fixtures/cooklang/, passes in full (07, 3B, 10).

Deliberate exclusions: none. Every case in canonical.yaml is parametrized below.
Should one ever be excluded, add its name to EXCLUDED with the reason, and record
the same in fixtures/cooklang/README.md.

The suite describes a recipe as a list of steps, each a list of items of type
text, ingredient, cookware or timer, plus a flat metadata mapping. `_adapt`
maps this project's `Recipe` onto that shape; the comparison then checks every
key the suite states for an item and ignores keys it leaves out (cookware
items sometimes carry `units: ""` and sometimes not).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.recipes.cooklang import (
    Cookware,
    IngredientRef,
    Item,
    Quantity,
    QuantityNumber,
    QuantityText,
    Recipe,
    Text,
    Timer,
    parse,
)

SUITE = Path(__file__).resolve().parent / "fixtures" / "cooklang" / "canonical.yaml"
EXCLUDED: dict[str, str] = {}  # case name -> reason; empty on purpose


def _load_cases() -> list[tuple[str, dict[str, Any]]]:
    with SUITE.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return sorted(document["tests"].items())


CASES = _load_cases()


def _quantity(quantity: Quantity, when_none: object) -> object:
    if isinstance(quantity, QuantityNumber):
        return quantity.value
    if isinstance(quantity, QuantityText):
        return quantity.text
    if quantity.kind == "range":
        return f"{quantity.low}-{quantity.high}"
    return when_none


def _adapt_item(item: Item) -> dict[str, object]:
    if isinstance(item, Text):
        return {"type": "text", "value": item.value}
    if isinstance(item, IngredientRef):
        adapted: dict[str, object] = {
            "type": "ingredient",
            "name": item.raw_name,
            "quantity": _quantity(item.quantity, "some"),
            "units": item.unit_text or "",
        }
        if item.optional:
            adapted["optional"] = True
        return adapted
    if isinstance(item, Cookware):
        adapted = {
            "type": "cookware",
            "name": item.name,
            "quantity": _quantity(item.quantity, Decimal(1)),
            "units": "",
        }
        if item.optional:
            adapted["optional"] = True
        return adapted
    assert isinstance(item, Timer)
    return {
        "type": "timer",
        "name": item.name or "",
        "quantity": _quantity(item.quantity, ""),
        "units": item.unit_text or "",
    }


def _adapt(recipe: Recipe) -> dict[str, object]:
    return {
        "steps": [[_adapt_item(item) for item in step.items] for step in recipe.steps],
        "metadata": dict(recipe.front_matter),
    }


def _expected_value(value: object) -> object:
    """The suite writes numbers as YAML numbers; the parser answers in Decimal."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    return value


@pytest.mark.parametrize(("name", "case"), CASES, ids=[name for name, _ in CASES])
def test_canonical_case(name: str, case: dict[str, Any]) -> None:
    if name in EXCLUDED:
        pytest.skip(EXCLUDED[name])
    recipe = parse(case["source"])
    assert isinstance(recipe, Recipe), f"{name}: {recipe}"
    actual = _adapt(recipe)
    expected = case["result"]

    assert actual["metadata"] == {
        str(k): _expected_value(v) for k, v in expected["metadata"].items()
    }

    actual_steps = actual["steps"]
    expected_steps = expected["steps"]
    assert len(actual_steps) == len(expected_steps), actual_steps
    for actual_step, expected_step in zip(actual_steps, expected_steps, strict=True):
        assert len(actual_step) == len(expected_step), actual_step
        for actual_item, expected_item in zip(actual_step, expected_step, strict=True):
            for key, value in expected_item.items():
                assert key in actual_item, (key, actual_item)
                assert actual_item[key] == _expected_value(value), (name, key, actual_item)


def test_every_case_is_covered() -> None:
    assert len(CASES) == 67
    assert not EXCLUDED
