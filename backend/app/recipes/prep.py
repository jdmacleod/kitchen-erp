"""Prep-word stripping for recipe names (07, 3C; the 1G amendment VS2). Pure: no I/O
beyond reading its own two data files once.

"minced garlic" means garlic, prepared; "ground cumin" is a different thing
from cumin. The first kind of word is a *prep word*, the second a *form word*,
and both lists are data files beside this module (``data/README.md``). Only
prep words are ever stripped, only from the start and the end of a name, and
what was stripped travels with the proposal so a decision can move it into the
line's note. Nothing here resolves a line (VC3): the caller looks the remainder
up and proposes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

_JOINERS = frozenset({"and"})


def _words(name: str) -> frozenset[str]:
    text = resources.files("app.recipes").joinpath("data", name).read_text(encoding="utf-8")
    out: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return frozenset(out)


@cache
def prep_words() -> frozenset[str]:
    """Words that say how a cook treats an ingredient; stripped from a name's ends."""
    words = _words("prep_words.txt")
    both = words & form_words()
    if both:
        raise ValueError(f"words listed as both prep and form: {sorted(both)}")
    return words


@cache
def form_words() -> frozenset[str]:
    """Words that are part of what is bought; never stripped."""
    return _words("form_words.txt")


@dataclass(frozen=True)
class Stripped:
    remainder: str
    words: tuple[str, ...]  # the stripped words in the order they were written

    @property
    def note(self) -> str:
        return " ".join(self.words)


def strip_prep(name_norm: str) -> Stripped | None:
    """The name without its leading and trailing prep words, or None when nothing came off.

    Works on a normalized name (``app.catalog.names.normalize_name``). A form
    word stops the stripping where it stands, so "ground cumin" is untouched and
    "chopped dried apricots" gives "dried apricots". A name is never stripped to
    nothing or to a joiner alone.
    """
    tokens = name_norm.split()
    if len(tokens) < 2:
        return None
    prep = prep_words()
    start = 0
    end = len(tokens)
    while end - start > 1 and tokens[start] in prep:
        start += 1
    while end - start > 1 and tokens[end - 1] in prep:
        end -= 1
    kept = tokens[start:end]
    if start == 0 and end == len(tokens):
        return None
    if all(word in _JOINERS for word in kept):
        return None
    words = tuple(tokens[:start] + tokens[end:])
    # A joiner at the edge of what was stripped is not a note ("and").
    while words and words[0] in _JOINERS:
        words = words[1:]
    while words and words[-1] in _JOINERS:
        words = words[:-1]
    if not words:
        return None
    return Stripped(remainder=" ".join(kept), words=words)


@cache
def describing_words() -> frozenset[str]:
    """Words that say which kind of a food, never which food (#516)."""
    return _words("describing_words.txt")


_FOOD_WORD = re.compile(r"[a-z0-9]+")
# A part or shape, not a food: "fellwort leaf" shares nothing with "lantern leaves".
_PARTS = frozenset({"leaf", "leaves", "green", "greens", "root", "stem", "stalk", "piece"})


def _singulars(word: str) -> set[str]:
    """Readings of a plural ("leaves" → leaf, "berries" → berry); a word itself first."""
    out = {word}
    if word.endswith("ies"):
        out.add(word[:-3] + "y")
    if word.endswith("ves"):
        out.add(word[:-3] + "f")
    if word.endswith("es"):
        out.add(word[:-2])
    if word.endswith("s") and not word.endswith("ss"):
        out.add(word[:-1])
    return out


def _food_words(text: str | None) -> set[str]:
    skip = describing_words() | prep_words() | _PARTS | _JOINERS
    out: set[str] = set()
    for word in _FOOD_WORD.findall((text or "").casefold()):
        if word not in skip:
            out |= _singulars(word)
    return out - skip


def shares_food_word(name: str, *others: str | None) -> bool:
    """Whether any of ``others`` shares a word naming a food with ``name`` (#516):
    "torn basil" and "Thai basil" do, "golden syrup glaze" and "golden beets"
    don't, so a proposal that matched only a describing word is never offered."""
    mine = _food_words(name)
    return any(mine & _food_words(o) for o in others if o)
