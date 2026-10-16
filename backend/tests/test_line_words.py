"""Pairing a receipt line's printed words with the product name they abbreviate (#388)."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.line_words import align


@pytest.mark.parametrize(
    ("printed", "name", "pairs"),
    [
        ("RVRBND BREAD FLR 2KG", "Riverbend bread flour",
         [("rvrbnd", "riverbend"), ("flr", "flour")]),
        ("BNLS CHKN BRST", "Boneless chicken breast",
         [("bnls", "boneless"), ("chkn", "chicken"), ("brst", "breast")]),
        ("HRBR LT GRK YGRT", "Greek yogurt", [("grk", "greek"), ("ygrt", "yogurt")]),
        ("CHK", "Chicken chunks", []),  # two name words fit: learn nothing
        ("EGGS LG", "Large egg", [("lg", "large")]),  # "eggs" is the name's word, plural
        ("CLOVER HONEY 16 OZ", "Clover honey", []),  # unit codes and numbers teach nothing
        ("PAPER TWL 6RL", "Paper towels", [("twl", "towels")]),
        ("VGRT", "Yogurt", []),  # an OCR misread keeps no first letter
        ("OF", "Bag of oats", []),  # glue words aren't expansions
    ],
)  # fmt: skip
def test_each_printed_word_pairs_with_the_one_name_word_it_abbreviates(printed, name, pairs):
    assert align(printed, name) == pairs


def _is_abbreviation(word: str, expansion: str) -> bool:
    rest = iter(expansion)
    return word[0] == expansion[0] and len(word) < len(expansion) and all(c in rest for c in word)


_WORDS = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=9), max_size=6
)


@given(printed=_WORDS, name=_WORDS)
def test_a_pair_is_always_a_true_abbreviation_of_a_name_word(printed, name):
    name_text = " ".join(name)
    pairs = align(" ".join(printed).upper(), name_text)
    name_words = set(re.findall(r"[a-z]+", name_text))
    for word, expansion in pairs:
        assert word in printed
        assert expansion in name_words
        assert word not in name_words
        assert _is_abbreviation(word, expansion)
    assert len({w for w, _ in pairs}) == len(pairs)
    assert align(" ".join(printed).upper(), name_text) == pairs
