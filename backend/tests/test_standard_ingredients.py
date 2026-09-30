"""The standard list's structure (03, 1G, criterion 79). References are checked by
`kerp ingredients check`, not here: CI has no USDA release."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.catalog import categories
from app.catalog.names import normalize_name
from app.catalog.standard import StandardListError, by_key, parse, standard_list

HEADER = "format: kitchen-erp-standard-ingredients/1\ningredients:\n"


def test_the_tracked_list_validates():
    entries = standard_list().ingredients
    assert len(entries) > 100
    assert all(categories.key(e.category) == e.category for e in entries)


def test_the_review_decisions_are_in_the_list():
    keys = set(by_key())
    # Flat: the abstract parents are gone (VC6).
    assert not {"butter", "milk", "cheese", "chicken", "vinegar", "cooking-oil"} & keys
    # VI1 beans split; VI2 lemon parts; VI3 drinks; VI4 toasted sesame oil.
    assert {
        "dried-black-beans",
        "canned-black-beans",
        "dried-kidney-beans",
        "canned-kidney-beans",
    } <= keys
    assert {"lemon", "bottled-lemon-juice"} <= keys and not {"lemon-juice", "lemon-zest"} & keys
    assert {"coffee", "red-wine", "white-wine", "beer", "vodka", "rum", "whiskey"} <= keys
    assert {"sesame-oil", "toasted-sesame-oil"} <= keys


def test_proper_nouns_are_the_only_capitals():
    capitalized = {e.key for e in standard_list().ingredients if e.name != e.name.lower()}
    assert capitalized == {e.key for e in standard_list().ingredients if e.proper_noun}


def _one(**fields) -> str:
    body = {"key": "leek", "name": "leek", "category": "produce", "unit": "g", **fields}
    lines = [
        f"  - {k}: {v}" if i == 0 else f"    {k}: {v}" for i, (k, v) in enumerate(body.items())
    ]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        (_one() + _one(), "appears twice"),
        (_one(name="Leek"), "not lowercase"),
        (_one() + _one(key="leeks", name="leek!"), "is also leek's"),
        (_one(spellings="[leek]"), "its own name"),
        (_one() + _one(key="ramp", name="ramp", spellings="[Leek]"), "is leek's name"),
        (
            _one(spellings="[wild onion]")
            + _one(key="ramp", name="ramp", spellings="[wild onion]"),
            "also leek's",
        ),
        (_one(key="ramp") + _one(key="leek", name="leek2"), "not sorted"),
    ],
)
def test_cross_entry_rules(entries, problem):
    with pytest.raises(StandardListError, match=problem):
        parse(HEADER + entries)


@pytest.mark.parametrize(
    "entry",
    [
        _one(key="Leek"),
        _one(key="local.leek"),
        _one(category="vegetables"),
        _one(unit="lb"),
        _one(colour="green"),
        _one(measures="[{label: stalk, qty: 80}]"),
    ],
)
def test_entry_shape(entry):
    with pytest.raises(ValidationError):
        parse(HEADER + entry)


def test_measure_quantities_are_decimal_text():
    parsed = parse(HEADER + _one(measures='[{label: stalk, qty: "80.5"}]'))
    assert str(parsed.ingredients[0].measures[0].qty) == "80.5"


def test_no_spelling_normalizes_to_nothing():
    for e in standard_list().ingredients:
        assert all(normalize_name(s) for s in e.spellings)
