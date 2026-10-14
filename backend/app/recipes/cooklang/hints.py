"""Lines that read as a reference but are written as an ingredient (issue 367).

`@sauce{1%portion}` in a recipe with a `= Sauce` section, or `@noodles{1%portion}`
after `@noodles{1%lb}`, become ingredient lines of their own: unmatched, or a
measure that never converts. `kerp recipes lint` lists them with the `@&` form
that says what was meant. Pure, like the parser.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.recipes.cooklang.model import IngredientRef, Recipe
from app.recipes.cooklang.references import name_key
from app.units.parse import UnitParseFailure


@dataclass(frozen=True, slots=True)
class ReferenceHint:
    line: int
    written: str  # the name as written
    suggestion: str  # the `@&` form to write instead
    why: str


def _quantity_text(ref: IngredientRef) -> str:
    return "{…}" if ref.unit_text or ref.quantity.kind != "none" else "{}"


def reference_hints(recipe: Recipe) -> list[ReferenceHint]:
    """Each ingredient line that names an earlier section, or repeats an earlier
    ingredient in a measure of its own that doesn't convert."""
    hints: list[ReferenceHint] = []
    sections: dict[str, int] = {}
    first: dict[str, IngredientRef] = {}
    for number, section in enumerate(recipe.sections, start=1):
        for step in section.steps:
            for ref in step.ingredients:
                key = name_key(ref.raw_name)
                if ref.reference:
                    continue
                if key in sections:
                    hints.append(
                        ReferenceHint(
                            ref.line,
                            ref.raw_name,
                            f"@&(={sections[key]}){ref.raw_name}{_quantity_text(ref)}",
                            f"names section {sections[key]}",
                        )
                    )
                elif key in first and _new_measure(first[key], ref):
                    hints.append(
                        ReferenceHint(
                            ref.line,
                            ref.raw_name,
                            f"@&{ref.raw_name}{{}}",
                            f"repeats the ingredient from line {first[key].line}",
                        )
                    )
                first.setdefault(key, ref)
        if section.name:
            sections.setdefault(name_key(section.name), number)
    return hints


def _forms(unit: str | None) -> set[str]:
    """A measure word with its plural folded, so "clove" and "cloves" are one measure."""
    key = name_key(unit or "")
    return {key, key.removesuffix("s"), key.removesuffix("es")}


def _new_measure(earlier: IngredientRef, later: IngredientRef) -> bool:
    """A repeat in a measure that doesn't convert and isn't the first one's ("1 portion")."""
    return isinstance(later.unit, UnitParseFailure) and not (
        _forms(earlier.unit_text) & _forms(later.unit_text)
    )
