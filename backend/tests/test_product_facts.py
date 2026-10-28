"""What a product's words say about its attributes (03, 1H amendment 2026-10-27).

An ingredient is a commodity; a product carries its variety, organic growing and its
meat or seafood facts. Brands and products are invented; cultivar and species names
are the trade's public names.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog import product_facts as facts
from app.catalog.attributes import fitting, validate_attributes
from app.catalog.sameness import Facts, attribute_facts, compare
from app.catalog.standard_match import match_entry


@pytest.mark.parametrize(
    ("name", "ingredient", "expected"),
    [
        ("Organic SweeTango apples", "apple", {"organic": True, "variety": "SweeTango"}),
        ("Granny Smith apple", "apple", {"variety": "Granny Smith"}),
        ("Fernhill Honeycrisp apples, 3 lb bag", "apples", {"variety": "Honeycrisp"}),
        ("Yukon Gold potatoes", "potato", {"variety": "Yukon Gold"}),
        ("Petite gold potatoes", "potato", {}),  # "gold" alone names no cultivar
        ("Hass avocado", "avocado", {"variety": "Hass"}),
        ("Gala apple", "pear", {}),  # a cultivar of another commodity
        ("Heirloom navel orange", "orange", {"variety": "Heirloom Navel"}),  # longest first
    ],
)
def test_produce_names_give_variety_and_organic(name, ingredient, expected):
    assert facts.facts_for("produce", name, ingredient) == expected


@pytest.mark.parametrize(
    ("name", "ingredient", "expected"),
    [
        (
            "Tarn bone-in skin-on hen thighs, chicken",
            "chicken thigh",
            {"species": "chicken", "bone": "bone_in", "skin": "skin_on"},
        ),
        (
            "Fernhill 85/15 beef, ground",
            "ground beef",
            {"species": "beef", "form": "ground", "lean_pct": 85},
        ),
        (
            "Fernhill 93% lean / 7% fat turkey",
            "ground turkey",
            {"species": "turkey", "form": "ground", "lean_pct": 93},
        ),
        ("USDA Choice sirloin cap", "sirloin", {"species": "beef", "grade": "choice"}),
        # A store brand with "Select" in it is no USDA grade.
        (
            "Tarn Select boneless hen thighs",
            "chicken thigh",
            {"species": "chicken", "bone": "boneless"},
        ),
        ("Shaved lamb leg, Tarn", "lamb leg", {"species": "lamb", "form": "sliced"}),
        ("Pancetta cubes, diced", "pancetta", {"species": "pork", "form": "cubed"}),
        (
            "Organic boneless skinless hen thighs",
            "chicken thigh",
            {"species": "chicken", "bone": "boneless", "skin": "skinless", "organic": True},
        ),
        ("Harbor board of cured things", "charcuterie", {}),  # no species: nothing guessed
    ],
)
def test_meat_names_give_species_and_cut_facts(name, ingredient, expected):
    assert facts.facts_for("meat", name, ingredient) == expected


@pytest.mark.parametrize(
    ("name", "ingredient", "expected"),
    [
        (
            "Fernhill gulf shrimp, peeled, 21/25",
            "shrimp",
            {"species": "shrimp", "form": "peeled", "count_size": "21/25"},
        ),
        ("Wild prawns U15", "shrimp", {"species": "shrimp", "origin": "wild", "count_size": "U15"}),
        (
            "Tarn wild coho, cold smoked",
            "salmon",
            {"species": "salmon", "species_detail": "coho", "origin": "wild", "form": "smoked"},
        ),
        (
            "Fernhill Atlantic salmon portions, fillet",
            "salmon",
            {"species": "salmon", "species_detail": "Atlantic", "form": "fillet"},
        ),
        ("Tarn anchovies in olive oil", "anchovies", {"species": "anchovy", "form": "canned"}),
        (
            "Yellowfin tuna steaks",
            "tuna",
            {"species": "tuna", "species_detail": "yellowfin", "form": "steak"},
        ),
    ],
)
def test_seafood_names_give_market_species_origin_and_count(name, ingredient, expected):
    # The farm is never guessed from the species: "Atlantic" names a species, not an origin.
    assert facts.facts_for("seafood", name, ingredient) == expected


def test_other_foods_take_only_organic():
    assert facts.facts_for("dairy", "Organic heavy cream", "heavy cream") == {"organic": True}
    assert facts.facts_for("pantry", "Tarn Organics black beans", "black beans") == {
        "organic": True
    }
    assert facts.facts_for("bakery", "Seeded rye loaf", "rye bread") == {}


@pytest.mark.parametrize(("lean", "fat"), [(80, 20), (85, 15), (93, 7)])
def test_lean_ratio_reads_both_ways_of_writing_it(lean, fat):
    assert facts.lean_pct(f"{lean}/{fat} ground pork") == lean
    assert facts.lean_pct(f"{lean}% lean ground pork") == lean
    assert facts.lean_pct(f"{lean}% / {fat}% ground pork") == lean


def test_a_long_run_of_spaces_reads_quickly():
    assert facts.lean_pct("80" + " " * 50_000 + "/x ground pork") is None
    assert facts.lean_pct("80   /   20 ground pork") == 80


def test_a_pair_that_is_no_ratio_is_not_a_lean_share():
    assert facts.lean_pct("Pack of 12/24 hot dogs") is None
    assert facts.lean_pct("Ground pork, 1 lb") is None


_WORDS = st.sampled_from(
    [
        "bone-in", "boneless", "skin-on", "skinless", "ground", "sliced", "diced", "choice",
        "prime", "organic", "wild", "farmed", "80/20", "93% lean", "26/30", "U10", "fillet",
        "smoked", "Fuji", "Yukon", "Gold", "Hass", "fresh", "Fernhill", "loin", "steak",
    ]
)  # fmt: skip
_CASES = st.sampled_from(
    [
        ("meat", "chicken thigh"), ("meat", "ground beef"), ("meat", "pork chop"),
        ("seafood", "salmon"), ("seafood", "shrimp"), ("produce", "apple"),
        ("produce", "potato"), ("dairy", "butter"), (None, "lark"),
    ]
)  # fmt: skip


@given(st.lists(_WORDS, max_size=8), _CASES)
def test_whatever_the_words_say_is_a_valid_attribute_set(words, case):
    category, ingredient = case
    found = facts.facts_for(category, " ".join(words), ingredient)
    assert validate_attributes(category, found) == found


def test_a_move_keeps_what_the_new_food_has_a_place_for():
    assert fitting("pantry", {"organic": True, "variety": "Fuji"}) == {"organic": True}
    assert fitting("seafood", {"species": "pork", "bone": "boneless"}) == {}
    # No species to hang details on, or a value the category refuses: organic stays.
    assert fitting("meat", {"organic": True, "grade": "large"}) == {"organic": True}
    assert fitting("produce", {"organic": True, "grade": "choice"}) == {"organic": True}
    assert fitting("seafood", {"organic": True, "variety": "Fuji"}) == {"organic": True}


def test_meat_and_seafood_take_organic_alone_but_no_other_detail_without_a_species():
    assert validate_attributes("meat", {"organic": True}) == {"organic": True}
    assert validate_attributes("seafood", {"organic": True}) == {"organic": True}
    for key, data in [("meat", {"bone": "boneless"}), ("seafood", {"origin": "wild"})]:
        with pytest.raises(ValueError, match="species is required"):
            validate_attributes(key, data)


@pytest.mark.parametrize(
    "name", ["Honeycrisp apple", "fuji", "Organic Pink Lady apples", "SweeTango apple"]
)
def test_a_cultivar_name_finds_its_commodity_entry(name):
    entry = match_entry(name)
    assert entry is not None and entry.key == "apple"


def test_types_recipes_call_for_by_name_keep_their_own_entries():
    for name, key in [
        ("red potato", "red-potato"),
        ("sweet potato", "sweet-potato"),
        ("Yukon Gold potatoes", "potato"),
    ]:
        entry = match_entry(name)
        assert entry is not None and entry.key == key, name


def _attrs(name, attributes):
    return Facts(name=name, **attribute_facts(attributes))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (_attrs("Fernhill apples", {"variety": "Fuji"}),
         _attrs("Fernhill apples", {"variety": "Gala"})),
        (_attrs("Ground beef", {"species": "beef", "lean_pct": 80}),
         _attrs("Ground beef", {"species": "beef", "lean_pct": 85})),
        (_attrs("Strip steak", {"species": "beef", "grade": "choice"}),
         _attrs("Strip steak", {"species": "beef", "grade": "prime"})),
        (_attrs("Salmon fillet", {"species": "salmon", "origin": "wild"}),
         _attrs("Salmon fillet", {"species": "salmon", "origin": "farmed"})),
    ],
)  # fmt: skip
def test_products_that_disagree_on_an_item_attribute_are_variants(a, b):
    verdict = compare(a, b)
    assert verdict.kind == "variant" and verdict.reasons == ("attributes",)
    assert compare(b, a).kind == "variant"


def test_a_meat_grade_is_an_attribute_not_a_word():
    # One product graded, the other not: nothing disagrees, so they stay the same.
    graded = _attrs("Strip steak", {"species": "beef", "grade": "choice"})
    assert compare(graded, _attrs("Strip steak", {"species": "beef"})).kind == "same"


def test_the_species_a_name_says_wins_over_the_one_its_cut_implies():
    assert facts.meat("Pork sirloin roast", "pork roast")["species"] == "pork"
    assert facts.meat("Lamb steak", "lamb chop")["species"] == "lamb"
    assert facts.meat("Tarn sirloin tips", "sirloin")["species"] == "beef"
    assert facts.meat("Organic tarn roast", "roast") == {"organic": True}


def test_a_lean_share_out_of_range_is_left_unset():
    assert facts.lean_pct("40% lean tarn blend") is None


def test_a_grade_in_one_name_is_answered_by_the_others_attribute():
    a = _attrs("Choice strip steak", {"species": "beef", "grade": "choice"})
    b = _attrs("Strip steak", {"species": "beef", "grade": "choice"})
    assert compare(a, b).kind == "same" and compare(b, a).kind == "same"
    # Without the attribute the word still tells them apart.
    assert compare(a, _attrs("Strip steak", {"species": "beef"})).kind != "same"
