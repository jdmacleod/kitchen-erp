"""Telling a duplicate from a different size or variant (04, 2P). Pure: no database, no I/O.

Two products, or a proposal and a product, are compared by their facts: name, brand,
pack, GTIN and ingredient. A name is reduced to a *name key*: lowercased, Unicode
normalized, without trademark signs, punctuation, a printed size, the product's own
brand or filler words. The comparison gives one of four verdicts:

- ``same``: the keys agree, the packs agree within 5% (or one is unknown), and
  the brands agree (or one is empty). Two different GTINs are never the same
  product. A different ingredient is reported but doesn't decide: two entries of one
  product are often filed under different ingredients, which a merge settles.
- ``other_size``: the keys agree and the packs disagree. A count against a weight or
  volume says nothing either way, and so many ounces against as many fluid ounces is
  one label read two ways; neither makes a different size.

Two names also agree when one has only a brand the other lacks: the other's brand
field, or a ", Brand" segment ending a brandless name; or a bare number the other
prints as part of a size (10 against 10").
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
from decimal import Decimal
from typing import Any, Literal

from app.catalog.extract import size_from_text
from app.catalog.proposals import _pack_grams, packs_disagree

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
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


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
    # Every number in the name's printed sizes: '10" 6 ct' gives 10 and 6, so a bare
    # "10" in another name, its inch mark lost, is not a difference.
    size_numbers: frozenset[str] = frozenset()


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
        size_numbers=frozenset(
            n for m in _SIZE.finditer(name) for n in _NUMBER.findall(m.group(0))
        ),
    )


def key_text(name: str, brand: str | None = None) -> str:
    """The identifying words in their printed order, for a search: "Fernhill Plum Jam,
    8 oz" with brand Fernhill searches as "plum jam", which a shorter catalog name
    matches where the whole title would not."""
    key = name_key(name, brand)
    seen: list[str] = []
    for word in _words(_SIZE.sub(" ", name)):
        stem = _stem(word)
        if stem in key.words and stem not in seen:
            seen.append(stem)
    return " ".join(seen)


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


def _tail(facts: Facts) -> frozenset[str]:
    """A brand written at the end of a brandless name ("Smoked tarn ham, Copperleaf"):
    the words of its last comma segment, printed size aside, when that segment is one to
    three words, none a number or a distinguishing word."""
    if facts.brand or "," not in facts.name:
        return frozenset()
    segment = _SIZE.sub(" ", facts.name).rsplit(",", 1)[1]
    words = [w for w in _words(segment) if w not in FILLER]
    if not 1 <= len(words) <= 3 or any(w.isdigit() or w in DISTINGUISHING for w in words):
        return frozenset()
    return frozenset(_stem(w) for w in words)


def _packs_disagree(pa: dict[str, Any], pb: dict[str, Any]) -> bool:
    """Two packs that say different sizes. A count against a weight or volume ("6 each"
    and "12 oz") says nothing either way, and the same number of ounces and fluid
    ounces is one label read two ways, so neither disagrees."""
    ga, gb = _pack_grams(pa), _pack_grams(pb)
    if ga is None or gb is None:
        return packs_disagree(pa, pb)
    if ga[0] != gb[0]:
        if "count" in (ga[0], gb[0]):
            return False
        units = {pa.get("unit"), pb.get("unit")}
        return not (units == {"oz", "fl_oz"} and Decimal(str(pa["qty"])) == Decimal(str(pb["qty"])))
    return packs_disagree(pa, pb)


def compare(a: Facts, b: Facts) -> Verdict:
    """The verdict on two products, symmetric in its kind."""
    ka, kb = name_key(a.name, a.brand), name_key(b.name, b.brand)
    # A word of the other product's brand ("Organic" in "Fernhill Organic") is not a
    # difference: that product's name lost it to its brand.
    # A brand written at the end of a brandless name is not a difference either, nor is a
    # bare number the other name prints as part of a size ("10" against '10"').
    brand_a, brand_b = set(_words(a.brand or "")), set(_words(b.brand or ""))
    tail_a, tail_b = _tail(a), _tail(b)
    only_a = tuple(
        sorted(
            w
            for w in ka.words - kb.words
            if w not in brand_b and w not in tail_a and w not in kb.size_numbers
        )
    )
    only_b = tuple(
        sorted(
            w
            for w in kb.words - ka.words
            if w not in brand_a and w not in tail_b and w not in ka.size_numbers
        )
    )
    if only_a or only_b:
        if all(w in DISTINGUISHING for w in only_a + only_b):
            return Verdict("variant", ("words",), only_a, only_b)
        return Verdict("similar", ("words",), only_a, only_b)
    pa, pb = _pack(a), _pack(b)
    if pa is not None and pb is not None and _packs_disagree(pa, pb):
        return Verdict("other_size", ("pack",))
    if a.gtin and b.gtin and a.gtin != b.gtin:
        return Verdict("variant", ("gtin",))
    if _brand(a) and _brand(b) and _brand(a) != _brand(b):
        return Verdict("similar", ("brand",))
    if a.ingredient_id and b.ingredient_id and a.ingredient_id != b.ingredient_id:
        return Verdict("same", ("words", "ingredient"))
    return Verdict("same", ("words",))
