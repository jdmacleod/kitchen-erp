"""An ingredient name the way the 1G rules write it (03 §1G, 12 §3; #345). Pure: no I/O.

The standard list follows the rules and CI checks it; a name a person types or a
file brings is brought to them here, and the UI shows the result before saving
("Will be saved as “frozen okra”") with a way to keep the name as typed:

- characters tidied as product names are (``product_names.tidy``);
- lowercase, except the proper nouns the standard list writes with a capital
  ("Dijon", "Thai", "New York");
- culinary word order: "okra, frozen" → "frozen okra"; a USDA-style "beef,
  flank steak" → the standard name it matches, or else "beef flank steak";
- no brand in front: "Harbor Light pepper relish" → "pepper relish", for the
  brands the caller knows.

Each change carries a reason a person can read. Applying it again changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache

from app.catalog import standard
from app.catalog.brand_names import is_brand_key, name_key
from app.catalog.product_names import tidy
from app.catalog.standard_match import match_entry

# A comma part that only says how a food was bought: it goes in front.
_FRONT_QUALIFIERS = frozenset(
    {
        "frozen", "canned", "dried", "fresh", "smoked", "sliced", "diced", "crushed",
        "ground", "whole", "roasted", "toasted", "pickled", "crispy", "fried", "cooked",
        "raw", "shredded", "grated", "minced", "chopped",
    }
)  # fmt: skip

# Meats a USDA-style name puts first: "beef, flank steak".
_MEATS = frozenset({"beef", "pork", "lamb", "veal", "chicken", "turkey", "duck", "goat"})

_WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*", re.UNICODE)


@dataclass(frozen=True)
class Conformed:
    name: str
    changes: list[str] = field(default_factory=list)


@cache
def proper_noun_words() -> frozenset[str]:
    """Every word the standard list capitalises, lowercased: "dijon", "thai", "york"."""
    return frozenset(w.lower() for phrase in _proper_nouns() for w in phrase)


@cache
def _proper_nouns() -> tuple[tuple[str, ...], ...]:
    """The capitalised words and phrases of the standard list's proper-noun names."""
    phrases: set[tuple[str, ...]] = set()
    for entry in standard.standard_list().ingredients:
        if not entry.proper_noun:
            continue
        run: list[str] = []
        for word in entry.name.split():
            if word[:1].isupper():
                run.append(word)
                continue
            if run:
                phrases.add(tuple(run))
            run = []
        if run:
            phrases.add(tuple(run))
    # Longest first, so "New York" is restored before a lone "New" would be.
    return tuple(sorted(phrases, key=len, reverse=True))


def _cased(name: str) -> str:
    """Lowercase, with the standard list's proper nouns given their capitals back."""
    words = name.lower().split(" ")
    out = list(words)
    for phrase in _proper_nouns():
        lowered = [w.lower() for w in phrase]
        n = len(phrase)
        for i in range(len(words) - n + 1):
            if words[i : i + n] == lowered:
                out[i : i + n] = list(phrase)
    return " ".join(out)


def _reordered(name: str) -> tuple[str, str | None]:
    """A comma name in culinary order, and why; the name unchanged when it isn't one."""
    head, comma, tail = name.partition(",")
    head, tail = head.strip(), tail.strip()
    if not comma or not head or not tail or "," in tail:
        return name, None
    tail_words = tail.lower().split()
    if head.lower() in _MEATS:
        entry = match_entry(tail)
        if entry is not None:
            return entry.name, f"“{head}, {tail}” is the standard name “{entry.name}”"
        return f"{head} {tail}", "the meat goes before its cut, without a comma"
    if all(w in _FRONT_QUALIFIERS for w in tail_words):
        return f"{tail} {head}", f"“{tail}” goes in front, as a cook writes it"
    return name, None


def _without_brand(name: str, brand_keys: Iterable[str]) -> tuple[str, str | None]:
    """The name without a leading brand the caller knows, and why."""
    keys = set(brand_keys)
    if not keys:
        return name, None
    words = name.split()
    # Longest leading run first; never the whole name.
    for n in range(len(words) - 1, 0, -1):
        lead = " ".join(words[:n])
        if (key := name_key(lead)) in keys and is_brand_key(key):
            rest = " ".join(words[n:])
            return rest, f"“{lead}” is a brand, which belongs on the product"
    return name, None


def conform(name: str, *, brand_keys: Iterable[str] = ()) -> Conformed:
    """The name as the 1G rules write it, with what changed and why."""
    changes: list[str] = []
    text = name
    for _ in range(3):  # tidy and spacing settle each other ("0\x1f:" → "0 :" → "0:")
        settled = " ".join((tidy(text) or "").split())
        if settled == text:
            break
        text = settled
    stripped, why = _without_brand(text, brand_keys)
    if why:
        changes.append(why)
    reordered, why = _reordered(stripped)
    if why:
        changes.append(why)
    cased = _cased(reordered)
    if cased != reordered and _WORD.search(reordered):
        changes.append("ingredient names are lowercase, except proper nouns")
    return Conformed(cased, changes)
