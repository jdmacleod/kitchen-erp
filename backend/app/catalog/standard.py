"""The standard list of ingredients (03, 1G): loading and validation. No database.

``standard_ingredients.yaml`` beside this module is a tracked, reviewed list
that the ingredient picker offers. It seeds nothing: an entry becomes an
ingredient only when a person picks it, and the entry's key then becomes that
ingredient's slug.

CI validates the file's structure (``validate``); whether its USDA references
exist in the loaded release is checked by ``kerp ingredients check``, because
the release is not in CI.
"""

from __future__ import annotations

from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.catalog.categories import CategoryKey
from app.catalog.names import STANDARD_KEY_RE, normalize_name

FORMAT = "kitchen-erp-standard-ingredients/1"
PATH = Path(__file__).with_name("standard_ingredients.yaml")


class StandardMeasure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=100)
    qty: Decimal = Field(gt=0)

    @field_validator("qty", mode="before")
    @classmethod
    def _qty_is_text(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("write a measure quantity as a quoted string, never a number")
        return value


class StandardEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    name: str = Field(min_length=1, max_length=200)
    proper_noun: bool = False
    category: CategoryKey
    unit: Literal["g", "ml", "each"]
    fdc: int | None = Field(default=None, gt=0)
    spellings: tuple[str, ...] = ()
    measures: tuple[StandardMeasure, ...] = ()
    note: str | None = None

    @field_validator("key")
    @classmethod
    def _key_shape(cls, value: str) -> str:
        if not STANDARD_KEY_RE.fullmatch(value):
            raise ValueError("a key is lowercase ASCII words joined by single hyphens")
        return value


class StandardList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["kitchen-erp-standard-ingredients/1"]
    usda_release: str | None = None
    ingredients: tuple[StandardEntry, ...]


class StandardListError(ValueError):
    pass


def validate(entries: tuple[StandardEntry, ...]) -> None:
    """Rules that span entries. Raises ``StandardListError`` naming every problem."""
    problems: list[str] = []
    keys: set[str] = set()
    names: dict[str, str] = {}
    for e in entries:
        if e.key in keys:
            problems.append(f"{e.key}: the key appears twice")
        keys.add(e.key)
        if not e.proper_noun and e.name != e.name.lower():
            problems.append(f"{e.key}: “{e.name}” is not lowercase and not marked proper_noun")
        norm = normalize_name(e.name)
        if norm in names:
            problems.append(f"{e.key}: the name “{e.name}” is also {names[norm]}'s")
        names[norm] = e.key
        labels = [m.label.lower() for m in e.measures]
        if len(labels) != len(set(labels)):
            problems.append(f"{e.key}: a measure label appears twice")
    spellings: dict[str, str] = {}
    for e in entries:
        for text in e.spellings:
            norm = normalize_name(text)
            if not norm:
                problems.append(f"{e.key}: the spelling “{text}” has no letters or digits")
            elif norm == normalize_name(e.name):
                problems.append(f"{e.key}: the spelling “{text}” is its own name")
            elif norm in names:
                problems.append(f"{e.key}: the spelling “{text}” is {names[norm]}'s name")
            elif norm in spellings and spellings[norm] != e.key:
                problems.append(f"{e.key}: the spelling “{text}” is also {spellings[norm]}'s")
            spellings[norm] = e.key
    if [e.key for e in entries] != sorted(e.key for e in entries):
        problems.append("entries are not sorted by key")
    if problems:
        raise StandardListError("; ".join(problems))


def parse(text: str) -> StandardList:
    """Parse and validate a standard list document."""
    data = yaml.safe_load(text)
    parsed = StandardList.model_validate(data)
    validate(parsed.ingredients)
    return parsed


@cache
def standard_list() -> StandardList:
    return parse(PATH.read_text(encoding="utf-8"))


@cache
def by_key() -> dict[str, StandardEntry]:
    return {e.key: e for e in standard_list().ingredients}


def entry(key: str) -> StandardEntry | None:
    return by_key().get(key)
