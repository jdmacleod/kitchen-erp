"""A likely spelling for a word OCR misread on a receipt line (2S, U9).

OCR turns one letter into another (PARSLEY into PARSLEV, SERRANO into SERRAND).
A word on an item line that the catalog does not know, and that is one edit
from exactly one word it does know, is offered as "maybe PARSLEY". Only a
suggestion: a person's tap changes the line's text, and the printed row stays.
"""

from __future__ import annotations

import re
import string
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ingredient, IngredientAlias, Product

# Shorter words are mostly abbreviations ("BBY", "ORG"), where one edit finds
# a different word as often as the right one.
_MIN_LEN = 5
_WORD = re.compile(r"[A-Za-z]+")
_LETTERS = string.ascii_lowercase


def words(texts: Iterable[str | None]) -> frozenset[str]:
    """Every word of the texts, casefolded, long enough to correct towards."""
    return frozenset(
        w.casefold() for text in texts if text for w in _WORD.findall(text) if len(w) >= _MIN_LEN
    )


def _one_edit(word: str) -> set[str]:
    """Every string one substitution, insertion or deletion away from ``word``."""
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    out = {a + b[1:] for a, b in splits if b}
    out |= {a + c + b[1:] for a, b in splits if b for c in _LETTERS if c != b[0]}
    out |= {a + c + b for a, b in splits for c in _LETTERS}
    return out


def _cased(maybe: str, like: str) -> str:
    return maybe.upper() if like.isupper() else maybe


def _known(key: str, vocabulary: frozenset[str]) -> bool:
    """A word the catalog has, or has in the singular or plural ("peppers", "pepper")."""
    forms = {key, key + "s", key + "es"}
    forms |= {key[:-1]} if key.endswith("s") else set()
    forms |= {key[:-2]} if key.endswith("es") else set()
    return bool(forms & vocabulary)


def suggest(text: str | None, vocabulary: frozenset[str]) -> list[dict[str, str]]:
    """The words of ``text`` with one likely spelling each, as {word, maybe}."""
    out: list[dict[str, str]] = []
    for word in _WORD.findall(text or ""):
        key = word.casefold()
        if len(key) < _MIN_LEN or _known(key, vocabulary):
            continue
        near = _one_edit(key) & vocabulary
        if len(near) == 1:
            out.append({"word": word, "maybe": _cased(near.pop(), word)})
    return out


async def vocabulary(db: AsyncSession) -> frozenset[str]:
    """The catalog's words: ingredient names and aliases, product names and brands."""
    rows = [
        *(await db.scalars(select(Ingredient.name))),
        *(await db.scalars(select(IngredientAlias.name_norm))),
        *(await db.scalars(select(Product.name))),
        *(await db.scalars(select(Product.brand))),
    ]
    return words(rows)
