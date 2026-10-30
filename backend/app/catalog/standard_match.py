"""Which standard-list entry an ingredient name means, if any (03, 1G). Pure: no I/O.

A wrong offer costs a reviewer more than no offer (#188), so the matcher never
guesses by similarity. A name is split into a head noun and its qualifiers:
the last word ("red wine vinegar" → vinegar; red, wine), or, for a name written
the USDA way with the noun first, the first part's last word ("vinegar, red
wine" → the same). An entry matches when:

- its head noun is the name's, singular or plural ("onions" meets "onion");
- every one of its qualifiers is among the name's, in any order;
- the name's other qualifiers are only words that don't change what the food
  is ("organic", "fresh", "large"; ``NOT_IDENTITY``), or how a cook readied it
  ("sliced", "freshly"; the recipes' prep words plus ``PREPARATION``). Two form
  words are let through too, but only for a head no entry's name splits by them
  (``FORM_WORDS``): "fine salt" is salt, while "toasted sesame oil" keeps its
  own entry. "ground" and "whole" always count: a food's ground and whole forms
  can sit under different heads (black pepper, black peppercorns). Callers with
  a prep tier of their own pass ``prep=False`` and keep the stripped words as the
  line's note.

A hyphen is a space to the matcher (#462): "all purpose flour" meets
"all-purpose flour". Stored names keep their hyphens.

So "wine vinegar" never gets "rice vinegar", a sausage never gets a cabbage,
and "smoked paprika" never gets plain "paprika". The most specific match wins;
two equally specific matches offer nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import cache

from app.catalog import standard
from app.catalog.names import normalize_name, plural, plural_word, singulars, spellings
from app.catalog.product_facts import variety_words
from app.recipes.prep import prep_words

# Words that describe how a food was bought, not which food it is. Data, not
# logic: add to it freely, but never a word that names a variety ("whole",
# "baby", "sweet"), because the matcher would then offer the plain food for it.
NOT_IDENTITY = frozenset(
    {
        "organic", "fresh", "natural", "premium", "fancy", "select", "pure",
        "large", "small", "medium", "jumbo", "extra", "original", "classic",
        "local", "imported", "family", "value", "size",
    }
)  # fmt: skip

# Readying words beyond the recipes' prep list (app/recipes/data/prep_words.txt)
# that never make a food another food (#463).
PREPARATION = frozenset({"cracked", "freshly"})

# Form words that name a food for some heads (toasted sesame oil, fine sea salt)
# and only describe it for others (fine salt, toasted almonds). Never "ground" or
# "whole": the matcher can't see a split across heads (black pepper, black
# peppercorns).
FORM_WORDS = frozenset({"fine", "toasted"})


@dataclass(frozen=True)
class _Form:
    head: str
    qualifiers: frozenset[str]
    entry: standard.StandardEntry
    spelling: bool  # one of the entry's spellings, not its name or the name's plural


def _head_keys(word: str) -> set[str]:
    """Every spelling a head noun may take, so singular and plural meet."""
    keys = {word, *singulars(word)}
    if (p := plural_word(word)) is not None:
        keys.add(p)
    # "chilies" meets "chiles", "yoghurt" meets "yogurt" (#345).
    return {variant for key in keys for variant in spellings(key)}


def _fold(text: str) -> str:
    """The normalized name with each hyphen read as a space."""
    return " ".join(normalize_name(text).replace("-", " ").split())


def split_name(name: str) -> tuple[str, frozenset[str]] | None:
    """(head noun, qualifiers) of a name, or None when nothing is left of it."""
    first, comma, rest = name.partition(",")
    head_words = _fold(first).split()
    if not head_words:
        return None
    qualifiers = set(head_words[:-1])
    if comma:
        qualifiers.update(_fold(rest).split())
    return head_words[-1], frozenset(qualifiers)


def _exact_key(name: str) -> str:
    """The name as one key, with a noun-first name turned round."""
    head, comma, tail = name.partition(",")
    if comma and tail.strip():
        return _fold(f"{tail} {head}")
    return _fold(name)


@cache
def _index() -> tuple[dict[str, standard.StandardEntry], dict[str, list[_Form]]]:
    """Built once, like the standard list it reads."""
    exact: dict[str, standard.StandardEntry] = {}
    by_head: dict[str, list[_Form]] = defaultdict(list)
    for e in standard.standard_list().ingredients:
        forms = [(e.name, False), *((text, True) for text in e.spellings)]
        if (p := plural(e.name)) is not None:
            forms.append((p, False))
        for form, is_spelling in forms:
            exact.setdefault(_fold(form), e)
            split = split_name(form)
            if split is None:
                continue
            head, qualifiers = split
            for key in _head_keys(head):
                by_head[key].append(_Form(head, qualifiers, e, is_spelling))
    return exact, dict(by_head)


def match_entry(name: str, *, prep: bool = True) -> standard.StandardEntry | None:
    """The standard entry this ingredient name means, or None. Never applied here.

    With ``prep=False`` a readying word blocks the match, as before #463.
    """
    exact, by_head = _index()
    for key in (_fold(name), _exact_key(name)):
        if key in exact:
            return exact[key]
    split = split_name(name)
    if split is None:
        return None
    head, qualifiers = split
    # A cultivar names a product of the commodity, not another food (03, 1H): "SweeTango
    # apple" is an apple.
    qualifiers -= variety_words(head, qualifiers)
    forms = [form for key in _head_keys(head) for form in by_head.get(key, ())]
    ignorable = NOT_IDENTITY
    if prep:
        ignorable |= prep_words() | PREPARATION
        # Only an entry's own name splits a head: "fine sea salt", a spelling of sea
        # salt, leaves "fine salt" as salt.
        if not any(form.qualifiers & FORM_WORDS for form in forms if not form.spelling):
            ignorable |= FORM_WORDS
    best: dict[str, standard.StandardEntry] = {}
    best_size = -1
    for form in forms:
        if not form.qualifiers <= qualifiers:
            continue
        if not (qualifiers - form.qualifiers) <= ignorable:
            continue
        size = len(form.qualifiers)
        if size > best_size:
            best, best_size = {form.entry.key: form.entry}, size
        elif size == best_size:
            best[form.entry.key] = form.entry
    return next(iter(best.values())) if len(best) == 1 else None
