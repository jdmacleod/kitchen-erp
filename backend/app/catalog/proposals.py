"""Merging the evidence for a product proposal (04, 2L). Pure: no database, no I/O.

Each piece of evidence is a *candidate*: a value for one field, from one source,
sometimes with a confidence. For each field the merge keeps the candidate whose
source ranks highest in that field's precedence list; confidence only breaks
ties within one source (PR12). Every other candidate is kept as an alternative.

A model's answer can only fill a field: it ranks below every other source, and
its confidence is capped (0.6 for text, 0.5 for a photo). Identity conflicts are
flagged and never resolved silently: two different GTINs, or two pack sizes
more than 5% apart. A person's choice settles a conflict, because it is not
silent.

Values are JSON-safe: decimals as strings (never floats), a pack as
``{"qty": "400", "unit": "g"}``, a GTIN as its GTIN-14.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.units.convert import convert_between
from app.units.table import Unit, units_by_code

SOURCES = (
    "person",
    "scan",
    "manufacturer",
    "usda_branded",
    "page_data",
    "adapter",
    "page_meta",
    "model",
    "address",
)

_IDENTITY = (
    "person",
    "scan",
    "manufacturer",
    "usda_branded",
    "page_data",
    "adapter",
    "page_meta",
    "model",
    "address",
)
_TITLE = (
    "person",
    "adapter",
    "page_data",
    "manufacturer",
    "usda_branded",
    "page_meta",
    "model",
    "address",
)
_LISTING = ("person", "adapter", "page_data", "page_meta", "model", "address")
_INGREDIENTS = ("person", "manufacturer", "usda_branded", "adapter", "model")

PRECEDENCE: Mapping[str, tuple[str, ...]] = {
    "brand": _IDENTITY,
    "gtin": _IDENTITY,
    "pack": _IDENTITY,
    "title": _TITLE,
    "category": _TITLE,
    "item_number": _LISTING,
    "item_code": _LISTING,
    "price": _LISTING,
    "average_weight": _LISTING,
    "on_sale": _LISTING,
    "store": _LISTING,
    "url": _LISTING,
    "ingredients_text": _INGREDIENTS,
}
FIELDS = tuple(PRECEDENCE)

MODEL_TEXT_CAP = Decimal("0.6")
MODEL_PHOTO_CAP = Decimal("0.5")
PACK_TOLERANCE = Decimal("0.05")
_UNITS: Mapping[str, Unit] = units_by_code()


class UnknownCandidate(ValueError):
    """A field this merge does not know, or a source that field never takes."""


@dataclass(frozen=True)
class Candidate:
    field: str
    value: Any
    source: str
    confidence: Decimal | None = None
    # Who brought it, when not the capture itself: "helper" for the products
    # helper's answers (2N), shown as the badge "Lookup helper".
    via: str | None = None


def model_confidence(value: Decimal | float | str | None, *, photo: bool) -> Decimal | None:
    """A model's confidence, capped: it can only ever fill a field."""
    if value is None:
        return None
    cap = MODEL_PHOTO_CAP if photo else MODEL_TEXT_CAP
    try:
        given = Decimal(str(value))
    except InvalidOperation:
        return None
    return max(Decimal(0), min(given, cap))


def _rank(candidate: Candidate) -> tuple[int, Decimal]:
    order = PRECEDENCE[candidate.field]
    return (order.index(candidate.source), -(candidate.confidence or Decimal(0)))


def _check(candidate: Candidate) -> None:
    order = PRECEDENCE.get(candidate.field)
    if order is None:
        raise UnknownCandidate(f"unknown field {candidate.field!r}")
    if candidate.source not in order:
        raise UnknownCandidate(f"{candidate.field} never comes from {candidate.source}")
    over = candidate.confidence is not None and candidate.confidence > MODEL_TEXT_CAP
    if candidate.source == "model" and over:
        raise UnknownCandidate("a model's confidence is over its cap")


def _pack_grams(pack: Any) -> tuple[str, Decimal] | None:
    """A pack as (dimension, quantity in the base unit), or None when it isn't one."""
    try:
        qty, unit = Decimal(str(pack["qty"])), _UNITS[pack["unit"]]
    except (KeyError, TypeError, InvalidOperation):
        return None
    return unit.dimension, convert_between(qty, unit.code, _base(unit.dimension), _UNITS)


def _base(dimension: str) -> str:
    return {"mass": "g", "volume": "ml"}.get(dimension, "each")


def packs_disagree(a: Any, b: Any) -> bool:
    """Two pack sizes more than 5% apart, or not comparable at all."""
    pa, pb = _pack_grams(a), _pack_grams(b)
    if pa is None or pb is None or pa[0] != pb[0]:
        return True
    low, high = sorted((pa[1], pb[1]))
    return high == 0 or (high - low) / high > PACK_TOLERANCE


def _conflicts(field: str, chosen: Candidate, others: list[Candidate]) -> bool:
    if chosen.source == "person":
        return False
    if field == "gtin":
        return any(o.value != chosen.value for o in others)
    if field == "pack":
        return any(packs_disagree(o.value, chosen.value) for o in others)
    return False


def _as_json(c: Candidate) -> dict[str, Any]:
    out: dict[str, Any] = {"value": c.value, "source": c.source}
    if c.confidence is not None:
        out["confidence"] = str(c.confidence)
    if c.via is not None:
        out["via"] = c.via
    return out


def merge(candidates: Iterable[Candidate]) -> dict[str, dict[str, Any]]:
    """Each field's chosen value, its source, the alternatives and whether they conflict."""
    by_field: dict[str, list[Candidate]] = {}
    for c in candidates:
        _check(c)
        by_field.setdefault(c.field, []).append(c)
    out: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        found = by_field.get(field)
        if not found:
            continue
        ranked = sorted(found, key=_rank)
        chosen, rest = ranked[0], ranked[1:]
        # The same value from another source is corroboration, not an alternative.
        alternatives: list[Candidate] = []
        for c in rest:
            if (c.value, c.source) not in [(a.value, a.source) for a in alternatives]:
                alternatives.append(c)
        out[field] = {
            **_as_json(chosen),
            "alternatives": [_as_json(a) for a in alternatives],
            "conflict": _conflicts(field, chosen, rest),
        }
    return out


def candidates_of(fields: Mapping[str, Mapping[str, Any]]) -> list[Candidate]:
    """Back from merged fields to the candidates they were made of, to merge more in."""
    out: list[Candidate] = []
    for field, state in fields.items():
        for entry in (state, *state.get("alternatives", [])):
            confidence = entry.get("confidence")
            out.append(
                Candidate(
                    field,
                    entry["value"],
                    entry["source"],
                    Decimal(confidence) if confidence is not None else None,
                    entry.get("via"),
                )
            )
    return out


def with_person(
    fields: Mapping[str, Mapping[str, Any]], edits: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """The fields after a person's edits, which win over every other source (PV8)."""
    kept = [c for c in candidates_of(fields) if not (c.source == "person" and c.field in edits)]
    return merge([*kept, *(Candidate(f, v, "person") for f, v in edits.items())])


def value(fields: Mapping[str, Mapping[str, Any]], field: str) -> Any:
    state = fields.get(field)
    return state["value"] if state else None


def has_conflict(fields: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return [f for f, state in fields.items() if state.get("conflict")]
