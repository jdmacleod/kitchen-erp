"""Telling a duplicate from a different size or variant (04, 2P). Pure: no database, no I/O.

Two products, or a proposal and a product, are compared by their facts: name, brand,
pack, GTIN and ingredient. A name is reduced to a *name key*: lowercased, Unicode
normalized, without trademark signs, punctuation, a printed size, the product's own
brand or filler words. The comparison gives one of four verdicts:

- ``same``: the keys agree, the packs agree within 5% (or one is unknown), and
  the brands agree (or one is empty). Two different GTINs are never the same
  product. A different ingredient is reported but doesn't decide: two entries of one
  product are often filed under different ingredients, which a merge settles.
- ``other_size``: the keys agree and the packs disagree.
- ``variant``: the keys differ only by distinguishing words (red against white, hot
  against sweet), or the keys agree and the GTINs differ.
- ``similar``: anything else.

A verdict only labels; it never chooses. Review preselects nothing from it (criterion 74).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.catalog.extract import size_from_text
from app.catalog.proposals import packs_disagree

Kind = Literal["same", "other_size", "variant", "similar"]

# Words that say nothing about which product it is.
FILLER = frozenset({"a", "an", "and", "the", "of", "with", "made", "fresh", "original", "new"})

# Words that make a different product of the same name: a closed list, so a word not
# on it makes two names merely similar, never variants.
DISTINGUISHING = frozenset(
    {"red", "white", "green", "yellow", "black", "brown", "purple", "golden", "orange", "pink"}
    | {"hot", "mild", "medium", "spicy", "sweet", "extra", "sharp"}
    | {"salted", "unsalted", "whole", "skim", "lowfat", "nonfat", "reduced", "light", "lite"}
    | {"sliced", "shredded", "grated", "block", "crumbled", "cubed", "diced", "ground"}
    | {"smoked", "uncured", "cured", "roasted", "raw", "cooked", "boneless", "skinless"}
    | {"organic", "decaf", "plain", "unsweetened", "seedless", "large", "small", "jumbo"}
)

_MARKS = re.compile(r"[™®©℠]")
_APOSTROPHES = re.compile(r"['’`]")
# A printed size: "6 x 330 ml", "10 oz", "8 ct", "12 in", '12"'. Bounded like extract's.
_SIZE = re.compile(
    r"(?<![\d.])\d{1,6}(?:\.\d{1,4})?\s{0,2}(?:[x×]\s{0,2}\d{1,6}(?:\.\d{1,4})?\s{0,3})?"
    r'(?:fl\.?\s{0,2}oz|kg|mg|g|ml|l|lbs?|oz|ct|count|pk|pack|pcs|pieces|inch(?:es)?|in\b|")',
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z0-9%]+")


def _words(text: str) -> list[str]:
    # Marks first: NFKC would spell "\u2122" out as "TM".
    text = unicodedata.normalize("NFKC", _MARKS.sub(" ", text)).lower()
    text = _APOSTROPHES.sub("", text)
    return _WORD.findall(text)


def _stem(word: str) -> str:
    """Plural to singular, crudely and symmetrically: "buns" and "bun" agree."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss") and word.isalpha():
        return word[:-1]
    return word


@dataclass(frozen=True)
class NameKey:
    words: frozenset[str]
    size_text: str | None


def name_key(name: str, brand: str | None = None) -> NameKey:
    """The words that identify a product, without its size, brand, marks or filler."""
    size = _SIZE.search(name)
    bare = _SIZE.sub(" ", name)
    words = _words(bare)
    brand_words = _words(brand or "")
    if brand_words:
        n = len(brand_words)
        for i in range(len(words) - n + 1):
            if words[i : i + n] == brand_words:
                words = words[:i] + words[i + n :]
                break
    return NameKey(
        words=frozenset(_stem(w) for w in words if w not in FILLER),
        size_text=size.group(0).strip() if size else None,
    )


@dataclass(frozen=True)
class Facts:
    name: str
    brand: str | None = None
    pack: dict[str, Any] | None = None
    gtin: str | None = None
    ingredient_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Verdict:
    kind: Kind
    reasons: tuple[str, ...]
    only_a: tuple[str, ...] = ()
    only_b: tuple[str, ...] = ()


def _pack(facts: Facts) -> dict[str, Any] | None:
    """The recorded pack, or one printed in the name."""
    return facts.pack or size_from_text(facts.name)[0]


def _brand(facts: Facts) -> str:
    return " ".join(_words(facts.brand or ""))


def compare(a: Facts, b: Facts) -> Verdict:
    """The verdict on two products, symmetric in its kind."""
    ka, kb = name_key(a.name, a.brand), name_key(b.name, b.brand)
    # A word of the other product's brand ("Organic" in "Fernhill Organic") is not a
    # difference: that product's name lost it to its brand.
    brand_a, brand_b = set(_words(a.brand or "")), set(_words(b.brand or ""))
    only_a = tuple(sorted(w for w in ka.words - kb.words if w not in brand_b))
    only_b = tuple(sorted(w for w in kb.words - ka.words if w not in brand_a))
    if only_a or only_b:
        if all(w in DISTINGUISHING for w in only_a + only_b):
            return Verdict("variant", ("words",), only_a, only_b)
        return Verdict("similar", ("words",), only_a, only_b)
    pa, pb = _pack(a), _pack(b)
    if pa is not None and pb is not None and packs_disagree(pa, pb):
        return Verdict("other_size", ("pack",))
    if a.gtin and b.gtin and a.gtin != b.gtin:
        return Verdict("variant", ("gtin",))
    if _brand(a) and _brand(b) and _brand(a) != _brand(b):
        return Verdict("similar", ("brand",))
    if a.ingredient_id and b.ingredient_id and a.ingredient_id != b.ingredient_id:
        return Verdict("same", ("words", "ingredient"))
    return Verdict("same", ("words",))
