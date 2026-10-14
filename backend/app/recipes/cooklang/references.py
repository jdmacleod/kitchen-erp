"""Point each `@&` reference at what it names, or say why it can't (cooklang-rs extensions).

Parsing reads `@&(~1)dough{}` as written; this pass, run once the whole file is
read, turns "one step back" into a section and a step, and checks that the
target comes before the reference: an earlier step of the same section, an
earlier section, or an ingredient already named. A reference to anything else
is a `ParseError` at the reference, as a syntax error would be.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, NamedTuple

from app.recipes.cooklang.model import (
    IngredientRef,
    Item,
    ParseError,
    QuantityNone,
    Recipe,
    Reference,
    Section,
    Step,
)


def name_key(name: str) -> str:
    """A name as references compare it: case and spacing folded."""
    return " ".join(name.casefold().split())


def _error(item: IngredientRef | Reference, message: str) -> ParseError:
    return ParseError(message, item.line, item.span[0] + 1)


def _token(ref: Reference) -> str:
    if ref.target == "ingredient":
        return f"@&{ref.name}"
    where = ("=" if ref.target == "section" else "") + ("~" if ref.relative else "")
    return f"@&({where}{ref.number}){ref.name}"


def _point(ref: Reference, section: int, step: int) -> Reference | ParseError:
    """A step or section reference made absolute, if it points backwards."""
    here = section if ref.target == "section" else step
    there = here - ref.number if ref.relative else ref.number - 1
    if 0 <= there < here:
        if ref.target == "section":
            return replace(ref, section=there, step=None)
        return replace(ref, section=section, step=there)
    if ref.target == "section":
        return _error(ref, f"`{_token(ref)}` must name an earlier section")
    return _error(ref, f"`{_token(ref)}` must name an earlier step of this section")


_Named = dict[str, tuple[int, int]]  # an ingredient name -> (section, step) it was first named in


def _link(item: Item, first_named: _Named, si: int, sj: int) -> Item | ParseError:
    """One item with its reference pointed; an ingredient is remembered where first named."""
    if isinstance(item, IngredientRef):
        key = name_key(item.raw_name)
        if item.reference and key not in first_named:
            return _error(item, f"`@&{item.raw_name}` must name an earlier ingredient")
        first_named.setdefault(key, (si, sj))
        return item
    if not isinstance(item, Reference):
        return item
    if item.target != "ingredient":
        return _point(item, si, sj)
    found = first_named.get(name_key(item.name))
    if found is None:
        return _error(item, f"`{_token(item)}` must name an earlier ingredient")
    return replace(item, section=found[0], step=found[1])


def link_references(recipe: Recipe) -> Recipe | ParseError:
    """The recipe with every reference pointed at its target, or the first one that has none."""
    first_named: _Named = {}
    sections: list[Section] = []
    for si, section in enumerate(recipe.sections):
        steps: list[Step] = []
        for sj, step in enumerate(section.steps):
            items: list[Item] = []
            for item in step.items:
                linked = _link(item, first_named, si, sj)
                if isinstance(linked, ParseError):
                    return linked
                items.append(linked)
            steps.append(replace(step, items=tuple(items)))
        sections.append(replace(section, steps=tuple(steps)))
    return replace(recipe, sections=tuple(sections))


# --- reading `@&` while parsing -------------------------------------------------


class Target(NamedTuple):
    """`@&` alone, or `@&(…)` naming a step or section: `~N` steps back, `N` step
    N, `=N` section N, `=~N` sections back."""

    kind: Literal["ingredient", "step", "section"]
    number: int
    relative: bool


def reference_target(line: str, j: int, line_number: int) -> tuple[Target, int] | ParseError:
    """What follows `@&`, with the index of the name after it."""
    if line[j : j + 1] != "(":
        return Target("ingredient", 0, False), j
    close = line.find(")", j + 1)
    spec = line[j + 1 : close] if close >= 0 else ""
    section = spec.startswith("=")
    rest = spec.removeprefix("=")
    relative = rest.startswith("~")
    digits = rest.removeprefix("~")
    if close < 0 or not digits.isascii() or not digits.isdigit() or int(digits) < 1:
        return ParseError(
            "`@&(…)` takes ~N (steps back), N (step N), =N (section N) or =~N (sections back)",
            line_number,
            j + 1,
        )
    return Target("section" if section else "step", int(digits), relative), close + 1


def as_reference(item: Item, target: Target | None) -> Item:
    """The ingredient as the reference `@&` makes it; anything else unchanged.

    `@&flour{100%g}` stays an ingredient line, marked, so its amount adds to the
    first `@flour`; with no quantity, or pointing at a step or section, it is a
    `Reference`, which no ingredient line counts.
    """
    if target is None or not isinstance(item, IngredientRef):
        return item
    if target.kind == "ingredient" and not isinstance(item.quantity, QuantityNone):
        return replace(item, reference=True)
    return Reference(
        name=item.raw_name,
        target=target.kind,
        number=target.number,
        relative=target.relative,
        quantity=item.quantity,
        unit_text=item.unit_text,
        note=item.note,
        line=item.line,
    )
