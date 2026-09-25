"""categories.key(): free-text ingredient categories to the nine display keys (UI-1.7, D12)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog import categories
from app.catalog.categories import KEYS, SYNONYMS, key

GROUPS = {
    "produce": ["produce", "vegetable", "vegetables", "veg", "fruit", "fruits", "herbs", "greens"],
    "dairy": ["dairy", "cheese", "eggs", "milk"],
    "meat": ["meat", "poultry", "beef", "pork", "charcuterie"],
    "seafood": ["seafood", "fish", "shellfish"],
    "bakery": ["bakery", "bread"],
    "pantry": ["pantry", "dry goods", "grains", "canned", "baking", "oils", "condiments"],
    "spices": ["spices", "spice", "seasoning", "seasonings"],
    "beverages": ["beverages", "beverage", "drinks", "coffee", "tea"],
    "frozen": ["frozen"],
}


def test_there_are_nine_keys_and_the_groups_cover_every_synonym():
    assert len(KEYS) == 9 and set(GROUPS) == set(KEYS)
    assert {s for group in GROUPS.values() for s in group} == set(SYNONYMS)


@pytest.mark.parametrize(
    ("text", "expected"), [(s, k) for k, group in GROUPS.items() for s in group]
)
def test_each_synonym_maps_to_its_group(text, expected):
    assert key(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dairy", "dairy"),
        ("  FISH\t", "seafood"),
        ("Dry   Goods", "pantry"),
        ("dry\ngoods", "pantry"),
        ("drygoods", None),
    ],
)
def test_case_and_whitespace_do_not_matter(text, expected):
    assert key(text) == expected


@pytest.mark.parametrize("text", [None, "", "   "])
def test_nothing_has_no_key(text):
    assert key(text) is None


@pytest.mark.parametrize("text", ["pasta", "snacks", "vegetables!", "fish sauce", "tofu"])
def test_an_unknown_value_has_no_key(text):
    assert key(text) is None


synonyms = st.sampled_from(sorted(SYNONYMS))
spaces = st.text(alphabet=" \t\n", max_size=3)


@st.composite
def recased(draw, text):
    flips = draw(st.lists(st.booleans(), min_size=len(text), max_size=len(text)))
    return "".join(c.upper() if f else c for c, f in zip(text, flips, strict=True))


@given(synonyms.flatmap(lambda s: st.tuples(st.just(s), recased(s))), spaces, spaces)
def test_any_casing_and_padding_of_a_synonym_finds_its_key(pair, before, after):
    original, cased = pair
    assert key(before + cased + after) == SYNONYMS[original]


@given(st.one_of(st.none(), st.text(max_size=40)))
def test_the_result_is_a_key_or_none_and_is_idempotent(text):
    result = key(text)
    assert result is None or result in KEYS
    if result is not None:
        assert key(result) == result


@given(st.text(max_size=40))
def test_normalizing_first_changes_nothing(text):
    assert key(categories.normalize(text)) == key(text)
