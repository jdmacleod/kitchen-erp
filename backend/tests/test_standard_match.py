"""The head-noun matcher behind standard-name suggestions (03, 1G; #188). Pure, invented names.

A wrong offer costs a reviewer more than no offer, so each refusal below is a
name the old whole-name similarity fallback answered with a different food.
"""

from __future__ import annotations

import pytest

from app.catalog.standard_match import NOT_IDENTITY, PREPARATION, match_entry, split_name
from app.recipes.prep import prep_words


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
        ("dark rum", "dark-rum"),  # a style beats its spirit
        ("fresh orange juice", "orange-juice"),
        ("cocktail cherries", "cocktail-cherry"),
        ("cinnamon", "ground-cinnamon"),  # a bare spice is the ground one
        ("cinnamon sticks", "cinnamon-sticks"),
        ("whole black peppercorns", "black-peppercorns"),
        ("sugar", "granulated-sugar"),  # a bare staple
        ("organic sugar", "granulated-sugar"),
        ("peanut butter", "peanut-butter"),  # the more specific entry still wins
        ("palm sugar", "palm-sugar"),
        ("instant yeast", "instant-yeast"),
        ("00 flour", "tipo-00-flour"),
        ("minute tapioca", "instant-tapioca"),  # granules, not the starch
        ("all purpose flour", "all-purpose-flour"),  # a hyphen is a space (#462)
        ("self rising flour", "self-rising-flour"),
        ("five spice powder", "five-spice-powder"),
        ("semi-sweet chocolate", "semisweet-chocolate"),
        ("honey ginger syrup", "honey-ginger-syrup"),
        ("honey-ginger syrup", "honey-ginger-syrup"),
        ("ground black pepper", "black-pepper"),  # preparation is not identity (#463)
        ("freshly ground black pepper", "black-pepper"),
        ("cracked black pepper", "black-pepper"),
        ("fine salt", "salt"),
        ("chopped dark chocolate", "dark-chocolate"),
        ("sliced almonds", "almonds"),
        ("toasted sesame oil", "toasted-sesame-oil"),  # the more specific entry still wins
        ("ground cloves", "ground-cloves"),  # a head split by form keeps the form
        ("whole cloves", "whole-cloves"),
        ("whole milk", "whole-milk"),
        ("crushed red pepper", "red-pepper-flakes"),  # "crushed" is not preparation
    ],
)
def test_a_name_finds_its_entry(name, key):
    assert _key(name) == key


@pytest.mark.parametrize(
    "name",
    [
        "wine vinegar",  # was offered a distilled vinegar
        "lentil chips",  # a kind of chips the list lacks: no other chips are offered
        "milk",  # was offered a 2% milk: the entry is more specific than the name
        "smoked onion",  # the name has a qualifier the entry lacks
        "sweet paprika",
        "vermouth",  # sweet and dry are both more specific: neither is offered
        "garlic cloves",  # garlic is not a qualifier ground cloves allows
        "apple butter",  # a food word blocks the bare staple
        "pie crust",  # frozen and refrigerated are both more specific
        "fresh currants",  # berries, not the dried Zante grapes
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


def test_preparation_words_never_name_a_variety():
    assert {"whole", "ground", "toasted", "fine"}.isdisjoint(PREPARATION | prep_words())


def test_a_caller_with_its_own_prep_tier_keeps_the_old_refusal():
    assert match_entry("sliced almonds", prep=False) is None
    assert match_entry("cracked black pepper", prep=False) is None
    assert _key("almonds") == "almonds"
