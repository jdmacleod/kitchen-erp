"""Free text to unit code. "lbs", "Tbsp", "fluid ounces" resolve; nonsense reports failure."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.units.table import CASE_SENSITIVE_ALIASES, SEED_UNITS, Unit

_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class UnitParseFailure:
    text: str
    code: str = "unknown_unit"


def normalize_unit_text(text: str) -> str:
    """Strip, drop periods, collapse whitespace. Case is preserved for the caller."""
    return _WS.sub(" ", text.replace(".", " ")).strip()


def alias_index(units: Iterable[Unit] = SEED_UNITS) -> Mapping[str, str]:
    index: dict[str, str] = {}
    for unit in units:
        index[unit.code.lower()] = unit.code
        index[unit.code.replace("_", " ").lower()] = unit.code
        for alias in unit.aliases:
            index[alias.lower()] = unit.code
    return index


_DEFAULT_INDEX = alias_index()


def parse_unit(text: str, index: Mapping[str, str] | None = None) -> str | UnitParseFailure:
    """Return a unit code, or a typed failure. Never raises on input."""
    if not isinstance(text, str):
        return UnitParseFailure(str(text))
    normalized = normalize_unit_text(text)
    if not normalized:
        return UnitParseFailure(text)
    if normalized in CASE_SENSITIVE_ALIASES:
        return CASE_SENSITIVE_ALIASES[normalized]
    lookup = index if index is not None else _DEFAULT_INDEX
    code = lookup.get(normalized.lower())
    if code is None:
        return UnitParseFailure(text)
    return code
