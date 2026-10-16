"""The naming pass's suggested name, word by word (04, 2I; #384).

Each waiting row's printed words (pack size removed) are shown through what the
household already knows: this vendor's learned abbreviations (#388), a one-edit
spelling fix for an OCR misread, and the one catalog or standard-list word an
abbreviation can stand for. Every change is returned with the printed word, so
the page can show it and a person can undo it. Computed per request, never stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import standard
from app.catalog.line_words import Vocabulary, WordChange, suggest_words, unread
from app.models import Ingredient, IngredientAlias, Product
from app.services import line_words, spelling


def sentence(words: list[str]) -> str:
    """Sentence case: "rvrbnd bread flr" → "Rvrbnd bread flr"."""
    text = " ".join(words).lower()
    return text[:1].upper() + text[1:]


@dataclass
class NamingWords:
    """What one request's rows are named with, loaded once (#183)."""

    vocabulary: Vocabulary
    spelling: frozenset[str]
    learned: dict[uuid.UUID, dict[str, list[tuple[str, int]]]] = field(default_factory=dict)


async def load(db: AsyncSession) -> NamingWords:
    entries = standard.by_key().values()
    texts = [
        *(await db.scalars(select(Ingredient.name).where(Ingredient.active))),
        *(await db.scalars(select(IngredientAlias.name_norm))),
        *(await db.scalars(select(Product.name).where(Product.active))),
        *(e.name for e in entries),
        *(s for e in entries for s in e.spellings),
    ]
    return NamingWords(Vocabulary.of(texts), await spelling.vocabulary(db))


async def suggest(
    db: AsyncSession, known: NamingWords, vendor_id: uuid.UUID, words: list[str]
) -> tuple[str, list[WordChange], int]:
    """The suggested name for these printed words, each change made to them, and how
    many words it still shows as printed without knowing them (#389)."""
    if vendor_id not in known.learned:
        found = await line_words.expansions(db, vendor_id)
        known.learned[vendor_id] = {
            w: [(e.expansion, e.count) for e in exps] for w, exps in found.items()
        }
    spelled = {
        s["word"].casefold(): s["maybe"] for s in spelling.suggest(" ".join(words), known.spelling)
    }
    changes = suggest_words(
        words, learned=known.learned[vendor_id], spelled=spelled, vocabulary=known.vocabulary
    )
    shown = [w.lower() for w in words]
    for change in changes:
        shown[change.index] = change.shown
    return sentence(shown), changes, unread(words, changes, known.vocabulary)


async def complete(
    db: AsyncSession, vendor_id: uuid.UUID, prefix: str, limit: int = 6
) -> list[dict[str, str]]:
    """Words that complete the one being typed in a Name (#385): what this vendor's
    words were learned to mean first, most confirmed first, then the vocabulary's,
    shortest first."""
    start = prefix.strip().casefold()
    if len(start) < 2 or not start.isalpha():
        return []
    learned = await line_words.expansions(db, vendor_id)
    counts: dict[str, int] = {}
    for found in learned.values():
        for e in found:
            if e.expansion.startswith(start) and e.expansion != start:
                counts[e.expansion] = max(counts.get(e.expansion, 0), e.count)
    out = [{"word": w, "source": "learned"} for w in sorted(counts, key=lambda w: (-counts[w], w))]
    known = await load(db)
    seen = set(counts)
    rest = sorted(
        (w for w in known.vocabulary.words if w.startswith(start) and w != start and w not in seen),
        key=lambda w: (len(w), w),
    )
    out += [{"word": w, "source": "vocabulary"} for w in rest]
    return out[:limit]
