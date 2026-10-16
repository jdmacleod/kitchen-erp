"""What a receipt line's words stand for, learned from the names people give (#388).

A vendor prints the same abbreviations on every receipt: "YGRT" for yogurt,
"BNLS" for boneless. Naming one line teaches the whole word. ``align`` pairs each
printed word with the one word of the product's name it abbreviates, and pairs
nothing it can't be sure of. Pure: no database, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.catalog.names import singular_word
from app.units import UnitParseFailure, parse_unit

_LETTERS = re.compile(r"[a-z]+")
# A name word an abbreviation can stand for: shorter ones ("of", "in") are glue.
_MIN_EXPANSION = 3
_VOWELS = frozenset("aeiou")


def _abbreviates(word: str, expansion: str) -> bool:
    """True when ``word`` keeps ``expansion``'s first letter and the rest in order."""
    if len(word) >= len(expansion) or word[0] != expansion[0]:
        return False
    rest = iter(expansion[1:])
    return all(c in rest for c in word[1:])


def _printed_words(raw_text_norm: str) -> list[str]:
    """The line's words that could be abbreviations: letters only, no unit codes."""
    out: list[str] = []
    for token in raw_text_norm.lower().split():
        if len(token) < 2 or not token.isalpha():
            continue
        if not isinstance(parse_unit(token), UnitParseFailure):
            continue
        out.append(token)
    return out


def align(raw_text_norm: str, product_name: str) -> list[tuple[str, str]]:
    """Each printed word and the name word it abbreviates, when exactly one fits.

    A word already in the name (or its singular) teaches nothing, and neither does
    one that two name words could stand for: "CHK" against "chicken chunks".
    """
    name_words = list(dict.fromkeys(_LETTERS.findall(product_name.lower())))
    whole = set(name_words) | {singular_word(w) for w in name_words}
    candidates = [w for w in name_words if len(w) >= _MIN_EXPANSION]
    pairs: list[tuple[str, str]] = []
    for word in dict.fromkeys(_printed_words(raw_text_norm)):
        if word in whole or singular_word(word) in whole:
            continue
        fits = [w for w in candidates if _abbreviates(word, w)]
        if len(fits) == 1:
            pairs.append((word, fits[0]))
    return pairs


# --- a suggested name from the printed words (#384) -----------------------------


@dataclass(frozen=True)
class WordChange:
    """One printed word shown as another in the suggested name."""

    index: int
    printed: str
    shown: str
    source: Literal["learned", "spelling", "vocabulary"]


@dataclass(frozen=True)
class Vocabulary:
    """The words an abbreviation may stand for, indexed by first and last letter."""

    words: frozenset[str]
    by_ends: Mapping[tuple[str, str], tuple[str, ...]]

    @classmethod
    def of(cls, texts: Iterable[str | None]) -> Vocabulary:
        words = frozenset(
            w
            for text in texts
            if text
            for w in _LETTERS.findall(text.lower())
            if len(w) >= _MIN_EXPANSION
        )
        ends: dict[tuple[str, str], list[str]] = {}
        for w in sorted(words):
            ends.setdefault((w[0], w[-1]), []).append(w)
        return cls(words, {k: tuple(v) for k, v in ends.items()})

    def knows(self, word: str) -> bool:
        return word in self.words or singular_word(word) in self.words

    def expansion(self, word: str) -> str | None:
        """The one word ``word`` abbreviates, ending in the same letter.

        With no confirmation behind it, a guess is made only for a word shaped
        like an abbreviation: three letters or more, no vowel after the first
        ("YGRT", "MSTRD"), and half the expansion's letters at least. A word
        with vowels ("WINES", "CANS") may be a plain word, so it waits to be learned.
        """
        if len(word) < _MIN_EXPANSION or _VOWELS.intersection(word[1:]):
            return None
        fits = [
            w
            for w in self.by_ends.get((word[0], word[-1]), ())
            if len(word) * 2 >= len(w) and _abbreviates(word, w)
        ]
        return fits[0] if len(fits) == 1 else None


def _learned_expansion(word: str, learned: Mapping[str, Sequence[tuple[str, int]]]) -> str | None:
    """The most confirmed learned expansion, unless another is confirmed as often."""
    found = sorted(learned.get(word, ()), key=lambda e: -e[1])
    if not found or (len(found) > 1 and found[0][1] == found[1][1]):
        return None
    return found[0][0]


def suggest_words(
    words: Sequence[str],
    *,
    learned: Mapping[str, Sequence[tuple[str, int]]],
    spelled: Mapping[str, str],
    vocabulary: Vocabulary,
) -> list[WordChange]:
    """How each printed word should be shown, in order of trust.

    A word the vocabulary knows stays. Otherwise: this vendor's (or any vendor's)
    learned expansion, then a one-edit spelling fix, then the one vocabulary word
    it abbreviates. Two equally good answers mean no change, and a word the vendor
    taught two meanings equally often isn't guessed from the vocabulary either.
    ``learned`` maps a word to (expansion, count) pairs, ``spelled`` a word to its
    fix, both casefolded.
    """
    changes: list[WordChange] = []
    for i, printed in enumerate(words):
        word = printed.casefold()
        if not word.isalpha() or vocabulary.knows(word):
            continue
        if not isinstance(parse_unit(word), UnitParseFailure):
            continue
        if word in learned:
            # This vendor taught the word: its answer, or none when two tie.
            if shown := _learned_expansion(word, learned):
                changes.append(WordChange(i, word, shown, "learned"))
        elif shown := spelled.get(word):
            changes.append(WordChange(i, word, shown.casefold(), "spelling"))
        elif shown := vocabulary.expansion(word):
            changes.append(WordChange(i, word, shown, "vocabulary"))
    return changes
