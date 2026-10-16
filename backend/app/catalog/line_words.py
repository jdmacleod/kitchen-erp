"""What a receipt line's words stand for, learned from the names people give (#388).

A vendor prints the same abbreviations on every receipt: "YGRT" for yogurt,
"BNLS" for boneless. Naming one line teaches the whole word. ``align`` pairs each
printed word with the one word of the product's name it abbreviates, and pairs
nothing it can't be sure of. Pure: no database, no I/O.
"""

from __future__ import annotations

import re

from app.catalog.names import singular_word
from app.units import UnitParseFailure, parse_unit

_LETTERS = re.compile(r"[a-z]+")
# A name word an abbreviation can stand for: shorter ones ("of", "in") are glue.
_MIN_EXPANSION = 3


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
