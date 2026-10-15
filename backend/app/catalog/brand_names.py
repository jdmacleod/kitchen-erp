"""The name key two spellings of one brand share (spec 16, 2R).

Accents, trademark signs, case, apostrophes and punctuation are dropped, and
``&`` reads as "and": "Larkspur José's", "LARKSPUR JOSES" and "Larkspur Jose's®"
all give "larkspur joses". The kitchen-erp-brands dataset and the products
helper use the same rules, so a key worked out in one matches the others.
Pure: no I/O.
"""

from __future__ import annotations

import re
import unicodedata

_MARKS = str.maketrans({"®": " ", "™": " ", "©": " ", "℠": " "})
_APOSTROPHES = re.compile(r"['’‘`´]")
_NOT_WORD = re.compile(r"[^0-9a-z]+")


def name_key(text: str) -> str:
    """The key of a brand name; empty when nothing but punctuation is left."""
    text = unicodedata.normalize("NFKD", text.translate(_MARKS))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = _APOSTROPHES.sub("", text.replace("&", " and "))
    return _NOT_WORD.sub(" ", text).strip()


def leading_keys(text: str, longest: int) -> list[str]:
    """The keys of `text`'s leading runs of words, longest first, the whole key
    excluded, at most `longest` words each: "a b c" gives ["a b", "a"]."""
    words = name_key(text).split()
    return [" ".join(words[:n]) for n in range(min(len(words) - 1, longest), 0, -1)]


# Words that only describe a food and are never a brand, though a store page or a
# receipt may put one in the brand field ("Organic"): such a brand is cleared.
# Keys, as ``name_key`` writes them.
NEVER_BRANDS = frozenset(
    {
        "organic", "natural", "all natural", "fresh", "kosher", "halal", "vegan",
        "gluten free", "non gmo", "local", "bulk", "produce",
    }
)  # fmt: skip

# Words that describe a food at the front of its name, though some brand is called
# that too ("Stone" the brewery, "Wild" the snack): a name keeps them, so "stone
# ground mustard" keeps its "stone", but the brewery's products keep their brand.
DESCRIPTORS = NEVER_BRANDS | frozenset(
    {
        "stone", "stone ground", "whole", "wild", "raw", "plain", "original", "classic",
        "premium", "select", "choice", "prime", "large", "jumbo", "extra", "simply",
        "farm", "garden", "fresh market",
    }
)  # fmt: skip


def is_brand_key(key: str) -> bool:
    """Whether a key may be stripped from the front of a name as a brand."""
    return bool(key) and key not in DESCRIPTORS


def is_never_brand(text: str) -> bool:
    """Whether a brand only describes the food ("Organic") and so should be cleared."""
    return name_key(text) in NEVER_BRANDS
