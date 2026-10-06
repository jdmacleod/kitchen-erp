"""Which standard-list entry an ingredient name means, if any (03, 1G). Pure: no I/O.

A wrong offer costs a reviewer more than no offer (#188), so the matcher never
guesses by similarity. A name is split into a head noun and its qualifiers:
the last word ("red wine vinegar" → vinegar; red, wine), or, for a name written
the USDA way with the noun first, the first part's last word ("vinegar, red
wine" → the same). An entry matches when:

- its head noun is the name's, singular or plural ("onions" meets "onion");
- every one of its qualifiers is among the name's, in any order;
- the name's other qualifiers are only words that don't change what the food
  is ("organic", "fresh", "large"; ``NOT_IDENTITY``).

So "wine vinegar" never gets "rice vinegar", a sausage never gets a cabbage,
and "smoked paprika" never gets plain "paprika". The most specific match wins;
two equally specific matches offer nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import cache

from app.catalog import standard
from app.catalog.names import normalize_name, plural, plural_word, singulars

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


@dataclass(frozen=True)
class _Form:
    head: str
    qualifiers: frozenset[str]
    entry: standard.StandardEntry


def _head_keys(word: str) -> set[str]:
    """Every spelling a head noun may take, so singular and plural meet."""
    keys = {word, *singulars(word)}
    if (p := plural_word(word)) is not None:
        keys.add(p)
    return keys


def split_name(name: str) -> tuple[str, frozenset[str]] | None:
    """(head noun, qualifiers) of a name, or None when nothing is left of it."""
    first, comma, rest = name.partition(",")
    head_words = normalize_name(first).split()
    if not head_words:
        return None
    qualifiers = set(head_words[:-1])
    if comma:
        qualifiers.update(normalize_name(rest).split())
    return head_words[-1], frozenset(qualifiers)


def _exact_key(name: str) -> str:
    """The name as one key, with a noun-first name turned round."""
    head, comma, tail = name.partition(",")
    if comma and tail.strip():
        return normalize_name(f"{tail} {head}")
    return normalize_name(name)


@cache
def _index() -> tuple[dict[str, standard.StandardEntry], dict[str, list[_Form]]]:
    """Built once, like the standard list it reads."""
    exact: dict[str, standard.StandardEntry] = {}
    by_head: dict[str, list[_Form]] = defaultdict(list)
    for e in standard.standard_list().ingredients:
        forms = [e.name, *e.spellings]
        if (p := plural(e.name)) is not None:
            forms.append(p)
        for form in forms:
            exact.setdefault(normalize_name(form), e)
            split = split_name(form)
            if split is None:
                continue
            head, qualifiers = split
            for key in _head_keys(head):
                by_head[key].append(_Form(head, qualifiers, e))
    return exact, dict(by_head)


def match_entry(name: str) -> standard.StandardEntry | None:
    """The standard entry this ingredient name means, or None. Never applied here."""
    exact, by_head = _index()
    for key in (normalize_name(name), _exact_key(name)):
        if key in exact:
            return exact[key]
    split = split_name(name)
    if split is None:
        return None
    head, qualifiers = split
    best: dict[str, standard.StandardEntry] = {}
    best_size = -1
    for key in _head_keys(head):
        for form in by_head.get(key, ()):
            if not form.qualifiers <= qualifiers:
                continue
            if not (qualifiers - form.qualifiers) <= NOT_IDENTITY:
                continue
            size = len(form.qualifiers)
            if size > best_size:
                best, best_size = {form.entry.key: form.entry}, size
            elif size == best_size:
                best[form.entry.key] = form.entry
    return next(iter(best.values())) if len(best) == 1 else None
