"""The head-noun matcher behind standard-name suggestions (03, 1G; #188). Pure, invented names.

A wrong offer costs a reviewer more than no offer, so each refusal below is a
name the old whole-name similarity fallback answered with a different food.
"""

from __future__ import annotations

import pytest

from app.catalog.standard_match import NOT_IDENTITY, match_entry, split_name


def _key(name: str) -> str | None:
    entry = match_entry(name)
    return entry.key if entry is not None else None


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("red onion", "red-onion"),  # the name itself
        ("red onions", "red-onion"),  # its plural
        ("onions", "onion"),
        ("green onions", "scallion"),  # a spelling's plural
        ("butter, unsalted", "unsalted-butter"),  # USDA's noun first
        ("onion, red", "red-onion"),
        ("vinegar, red wine", "red-wine-vinegar"),  # qualifiers in any order
        ("organic red wine vinegar", "red-wine-vinegar"),  # a word that isn't identity
        ("large eggs", "egg"),
        ("Organic Carrots", "carrot"),
    ],
)
def test_a_name_finds_its_entry(name, key):
    assert _key(name) == key


@pytest.mark.parametrize(
    "name",
    [
        "wine vinegar",  # was offered a distilled vinegar
        "pita chips",  # was offered pistachios: a different head noun
        "milk",  # was offered a 2% milk: the entry is more specific than the name
        "smoked onion",  # the name has a qualifier the entry lacks
        "sweet paprika",
        "Quince paste",  # nothing on the list is a paste of quince
        "",
        "12 oz",
    ],
)
def test_no_guess_when_nothing_fits(name):
    assert match_entry(name) is None


def test_a_different_head_noun_is_never_offered():
    for name in ("savoy sausage", "cabbage sausage", "rice vinegar crackers"):
        entry = match_entry(name)
        assert entry is None or split_name(entry.name)[0] == split_name(name)[0]


def test_split_name_takes_the_head_noun_last_or_first_before_a_comma():
    assert split_name("Red Wine Vinegar") == ("vinegar", frozenset({"red", "wine"}))
    assert split_name("vinegar, red wine") == ("vinegar", frozenset({"red", "wine"}))
    assert split_name("cheese, cheddar, sharp") == ("cheese", frozenset({"cheddar", "sharp"}))
    assert split_name(" , ") is None


def test_words_that_name_a_variety_are_never_ignored():
    # "whole milk" is not "milk", and "sweet paprika" is not "paprika".
    assert {"whole", "baby", "sweet", "smoked", "red"}.isdisjoint(NOT_IDENTITY)
