"""Whether a standard entry is a kind of an ingredient, and which one a product's
name says it is (#525, the Tidy the catalog move check). Pure: no I/O.

A form is a name or a spelling, split by ``standard_match`` into a head noun and
qualifiers. An entry is narrower than an ingredient when one of the entry's forms
has the head of one of the ingredient's forms and a strict superset of its
qualifiers: "bourbon whiskey" (bourbon's spelling) is under "whiskey", "Scotch
whisky" is under "whiskey" through its spelling "whisky", "dark rum" is under
"rum", "curaçao orange liqueur" is under "orange liqueur". "orange juice" is not
under "orange liqueur": the heads differ.

This is a stand-in until ingredients have a parent (#525's ``parent_id``).
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from app.catalog import standard
from app.catalog.standard_match import _fold, _head_keys, _index, split_name


def entry_forms(entry: standard.StandardEntry) -> tuple[str, ...]:
    """The entry's name and its spellings."""
    return (entry.name, *entry.spellings)


# Folding and splitting are the cost of the inbox's count (#327); the standard
# list's forms and the household's ingredient names repeat, so both are kept.
@lru_cache(maxsize=8192)
def _split_one(name: str) -> tuple[str, frozenset[str]] | None:
    return split_name(name)


@lru_cache(maxsize=8192)
def _folded(name: str) -> str:
    return _fold(name)


def _split(names: Iterable[str]) -> list[tuple[str, frozenset[str]]]:
    return [s for name in names if (s := _split_one(name)) is not None]


def is_narrower(entry_names: Iterable[str], general_names: Iterable[str]) -> bool:
    """Whether some form of the entry has the head of some general form and a strict
    superset of its qualifiers."""
    general = _split(general_names)
    for head, qualifiers in _split(entry_names):
        keys = _head_keys(head)
        for g_head, g_qualifiers in general:
            if keys & _head_keys(g_head) and qualifiers > g_qualifiers:
                return True
    return False


def narrower_entries(
    general_names: Iterable[str], own_key: str | None = None
) -> list[standard.StandardEntry]:
    return list(_narrower(tuple(general_names), own_key))


@lru_cache(maxsize=4096)
def _narrower(
    general_names: tuple[str, ...], own_key: str | None
) -> tuple[standard.StandardEntry, ...]:
    """The standard entries narrower than the general forms, through the head index;
    never the general ingredient's own entry (``own_key``), whose spellings ("white
    rum") are narrower than its name."""
    _, by_head = _index()
    found: dict[str, standard.StandardEntry] = {}
    for head, qualifiers in _split(general_names):
        for key in _head_keys(head):
            for form in by_head.get(key, ()):
                if form.entry.key != own_key and form.qualifiers > qualifiers:
                    found.setdefault(form.entry.key, form.entry)
    return tuple(sorted(found.values(), key=lambda e: e.key))


def named_in(product_name: str, names: Iterable[str]) -> int | None:
    """The most qualifiers of the names found as whole words in the product's name
    (folded: case, hyphens and diacritics aside), or None when none is."""
    return _named_in_folded(f" {_folded(product_name)} ", names)


def _named_in_folded(text: str, names: Iterable[str]) -> int | None:
    best: int | None = None
    for name in names:
        folded = _folded(name)
        if not folded or f" {folded} " not in text:
            continue
        split = _split_one(name)
        size = len(split[1]) if split else 0
        best = size if best is None else max(best, size)
    return best


def best_move(
    product_name: str, candidates: Iterable[standard.StandardEntry]
) -> standard.StandardEntry | None:
    """Of the narrower entries, the one the product's name names with the most
    qualifiers; None when none is named or two tie."""
    text = f" {_folded(product_name)} "
    scored = [
        (size, e)
        for e in candidates
        if (size := _named_in_folded(text, entry_forms(e))) is not None
    ]
    if not scored:
        return None
    top = max(size for size, _ in scored)
    winners = [e for size, e in scored if size == top]
    return winners[0] if len(winners) == 1 else None
