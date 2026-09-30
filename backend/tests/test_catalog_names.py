"""The ingredient name key and the plural generator (03, 1G, criterion 75)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.names import (
    GENERATED_SLUG_PREFIX,
    STANDARD_KEY_RE,
    normalize_name,
    plural,
)

TEXT = st.text(max_size=60)
WORDS = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyzéñüç-", min_size=1, max_size=10),
    min_size=1,
    max_size=4,
).map(" ".join)


@given(TEXT)
def test_normalize_is_idempotent(text):
    once = normalize_name(text)
    assert normalize_name(once) == once


@given(WORDS)
def test_case_and_accents_do_not_change_the_key(text):
    assert normalize_name(text.upper()) == normalize_name(text)
    stripped = text.translate(str.maketrans("éñüç", "enuc"))
    assert normalize_name(stripped) == normalize_name(text)


@given(TEXT)
def test_key_has_no_edge_hyphens_or_extra_spaces(text):
    key = normalize_name(text)
    for word in key.split(" ") if key else []:
        assert word and not word.startswith("-") and not word.endswith("-")
    assert "  " not in key


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Jalapeño", "jalapeno"),
        ("Crème fraîche", "creme fraiche"),
        ("half-and-half", "half-and-half"),
        ("-green- onion!", "green onion"),
        ("Baker's yeast", "bakers yeast"),
        ("Tomatoes, diced 28 oz", "tomatoes diced"),
        ("500g flour", "flour"),
        ("1 1/2 cups sugar", "sugar"),
        ("½ cup milk", "milk"),
        ("12 fl oz beer", "beer"),
        ("1.5kg potatoes", "potatoes"),
        ("00 flour", "00 flour"),
        ("7 grain bread", "7 grain bread"),
        ("  Green   Onion ", "green onion"),
    ],
)
def test_normalize_examples(text, key):
    assert normalize_name(text) == key


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("egg", "eggs"),
        ("cherry tomato", "cherry tomatoes"),
        ("potato", "potatoes"),
        ("bay leaf", "bay leaves"),
        ("peach", "peaches"),
        ("radish", "radishes"),
        ("box", "boxes"),
        ("glass", "glasses"),
        ("berry", "berries"),
        ("turkey", "turkeys"),
        ("Scallion", "Scallions"),
        ("rice", None),
        ("olive oil", None),
        ("green beans", None),
        ("hummus", None),
        ("7up", None),
        ("jalapeño", None),
    ],
)
def test_plural_rules(name, expected):
    assert plural(name) == expected


def test_generated_slugs_can_never_be_standard_keys():
    assert STANDARD_KEY_RE.fullmatch("green-onion")
    assert not STANDARD_KEY_RE.fullmatch(GENERATED_SLUG_PREFIX + "green-onion")
