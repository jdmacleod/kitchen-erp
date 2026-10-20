"""Pairing a receipt line's printed words with the product name they abbreviate (#388)."""

from __future__ import annotations

import re

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from app.catalog.line_words import Vocabulary, align, suggest_words
from app.catalog.names import singular_word


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


# --- the suggested name (#384) --------------------------------------------------

_VOCAB = Vocabulary.of(
    ["Greek yogurt", "plain", "boneless chicken breast", "chickpeas", "flour", "braunschweiger"]
)


def _shown(words, learned=None, spelled=None):
    changes = suggest_words(words, learned=learned or {}, spelled=spelled or {}, vocabulary=_VOCAB)
    return [(c.index, c.printed, c.shown, c.source) for c in changes]


def test_a_known_word_stays_and_an_abbreviation_with_one_reading_expands():
    assert _shown(["GREEK", "YGRT", "PLN"]) == [
        (1, "ygrt", "yogurt", "vocabulary"),
        (2, "pln", "plain", "vocabulary"),
    ]


def test_a_learned_expansion_comes_first_and_a_tie_changes_nothing():
    learned = {"chk": [("chickpeas", 3), ("chicken", 1)], "brst": [("breast", 2), ("bristle", 2)]}
    # "chk" has two vocabulary readings, but this vendor taught one of them more often.
    assert _shown(["CHK", "BRST"], learned) == [(0, "chk", "chickpeas", "learned")]


def test_a_spelling_fix_comes_before_a_vocabulary_guess_and_units_stay():
    assert _shown(["FLOUP", "OZ"], spelled={"floup": "FLOUR"}) == [
        (0, "floup", "flour", "spelling")
    ]


def test_words_with_digits_or_two_readings_are_left_as_printed():
    assert _shown(["CHK", "5PK"]) == []  # chicken or chickpeas: no guess


def test_an_everyday_word_is_not_read_as_a_long_one():
    assert _shown(["BAR"]) == []  # b…r fits braunschweiger, but keeps too little of it


@given(words=_WORDS)
def test_every_change_names_a_printed_word_and_an_offered_reading(words):
    printed = [w.upper() for w in words]
    for change in suggest_words(printed, learned={}, spelled={}, vocabulary=_VOCAB):
        assert printed[change.index].casefold() == change.printed
        assert change.shown in _VOCAB.words
        assert _is_abbreviation(change.printed, change.shown)


# --- a vocabulary guess needs the only fit (#384) -------------------------------

_INVENTED = Vocabulary.of(
    [
        "Zonkel bars",
        "zinlo",
        "morvated",
        "mirtadel",
        "quembert",
        "quemberts",
        "plosk",
        "palisokarine",
    ]
)


@pytest.mark.parametrize(
    ("printed", "shown"),
    [
        ("ZNL", None),  # zonkel ends in l, but zinlo fits too
        ("MRTD", None),  # morvated or mirtadel: the one ending in d is no likelier
        ("ZNKL", "zonkel"),  # the only fit
        ("QMBRT", "quembert"),  # a word and its plural are one reading
        ("PLSK", "plosk"),  # four letters keep too little of palisokarine to fit it
        ("ZNKLS", None),  # nothing fits: "zonkels" isn't a known word
    ],
)
def test_a_vocabulary_word_is_shown_only_when_no_other_fits(printed, shown):
    changes = suggest_words([printed], learned={}, spelled={}, vocabulary=_INVENTED)
    assert [c.shown for c in changes] == ([shown] if shown else [])


def test_a_learned_word_still_wins_where_the_vocabulary_has_two_fits():
    learned = {"znl": [("zanderblin", 2)]}
    changes = suggest_words(["ZNL"], learned=learned, spelled={}, vocabulary=_INVENTED)
    assert [(c.shown, c.source) for c in changes] == [("zanderblin", "learned")]


@st.composite
def _two_readings(draw):
    """A consonant abbreviation and two words it abbreviates, vowels added."""
    word = draw(st.text(alphabet="bcdfghjklmnpqrstvwxz", min_size=3, max_size=6))
    pads = st.lists(st.text(alphabet="aeiou", max_size=1), min_size=len(word), max_size=len(word))
    tails = st.sampled_from(["", "e", "o"])
    readings = [
        "".join(c + p for c, p in zip(word, draw(pads), strict=True)) + draw(tails)
        for _ in range(2)
    ]
    return word, readings


@given(case=_two_readings(), others=_WORDS)
def test_with_two_vocabulary_words_fitting_no_vocabulary_change_is_made(case, others):
    word, readings = case
    vocabulary = Vocabulary.of([*readings, *others])
    assume(len({singular_word(w) for w in vocabulary.fits(word)}) > 1)
    changes = suggest_words([word.upper()], learned={}, spelled={}, vocabulary=vocabulary)
    assert [c for c in changes if c.source == "vocabulary"] == []
