"""The parsed shape of a Cooklang file. Frozen, decimal, and free of I/O.

A `Recipe` keeps its front matter verbatim, its sections in order, and every
step's items (text, ingredient, cookware, timer) in the order written. Line
numbers are carried for error reporting and excluded from equality, so that two
recipes with the same structure compare equal wherever they sit in a file.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from app.units.parse import UnitParseFailure


@dataclass(frozen=True, slots=True)
class QuantityNumber:
    value: Decimal
    kind: Literal["number"] = "number"


@dataclass(frozen=True, slots=True)
class QuantityRange:
    low: Decimal
    high: Decimal
    kind: Literal["range"] = "range"


@dataclass(frozen=True, slots=True)
class QuantityText:
    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True, slots=True)
class QuantityNone:
    kind: Literal["none"] = "none"


Quantity = QuantityNumber | QuantityRange | QuantityText | QuantityNone
NO_QUANTITY = QuantityNone()


@dataclass(frozen=True, slots=True)
class Text:
    value: str


@dataclass(frozen=True, slots=True)
class IngredientRef:
    raw_name: str
    quantity: Quantity = NO_QUANTITY
    unit_text: str | None = None
    unit: str | UnitParseFailure | None = None  # the 1B parser's answer for unit_text
    note: str | None = None
    optional: bool = False
    line: int = field(default=0, compare=False)


@dataclass(frozen=True, slots=True)
class Cookware:
    name: str
    quantity: Quantity = NO_QUANTITY
    optional: bool = False
    line: int = field(default=0, compare=False)


@dataclass(frozen=True, slots=True)
class Timer:
    name: str | None = None  # None for an anonymous timer such as `~{3%minutes}`
    quantity: Quantity = NO_QUANTITY
    unit_text: str | None = None
    line: int = field(default=0, compare=False)


Item = Text | IngredientRef | Cookware | Timer


@dataclass(frozen=True, slots=True)
class Step:
    items: tuple[Item, ...]
    line: int = field(default=0, compare=False)

    @property
    def ingredients(self) -> tuple[IngredientRef, ...]:
        return tuple(item for item in self.items if isinstance(item, IngredientRef))

    @property
    def cookware(self) -> tuple[Cookware, ...]:
        return tuple(item for item in self.items if isinstance(item, Cookware))

    @property
    def timers(self) -> tuple[Timer, ...]:
        return tuple(item for item in self.items if isinstance(item, Timer))


@dataclass(frozen=True, slots=True)
class Section:
    name: str | None  # None for the steps before any `=` header
    steps: tuple[Step, ...]
    line: int = field(default=0, compare=False)


@dataclass(frozen=True, slots=True)
class Recipe:
    front_matter: Mapping[str, object]
    sections: tuple[Section, ...]
    title: str | None = None
    servings: Decimal | None = None
    servings_text: str | None = None

    @property
    def steps(self) -> tuple[Step, ...]:
        return tuple(step for section in self.sections for step in section.steps)

    def _items(self) -> Iterator[tuple[Section, Item]]:
        for section in self.sections:
            for step in section.steps:
                for item in step.items:
                    yield section, item

    @property
    def ingredients(self) -> tuple[IngredientRef, ...]:
        return tuple(item for _, item in self._items() if isinstance(item, IngredientRef))

    @property
    def ingredient_lines(self) -> tuple[tuple[str | None, IngredientRef], ...]:
        """Every ingredient reference with the name of the section it sits in."""
        return tuple(
            (section.name, item)
            for section, item in self._items()
            if isinstance(item, IngredientRef)
        )

    @property
    def cookware(self) -> tuple[Cookware, ...]:
        return tuple(item for _, item in self._items() if isinstance(item, Cookware))

    @property
    def timers(self) -> tuple[Timer, ...]:
        return tuple(item for _, item in self._items() if isinstance(item, Timer))


@dataclass(frozen=True, slots=True)
class ParseError:
    """Where a file stopped making sense. Lines and columns are 1-based."""

    message: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"line {self.line}, column {self.column}: {self.message}"
