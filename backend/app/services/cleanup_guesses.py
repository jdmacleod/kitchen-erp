"""Tidy the catalog's slow guesses, kept per ingredient name (issue 372).

Two of the page's checks search the USDA foods for each ingredient they look at:
the category of an ingredient with none (when nothing quicker settles it), and a
food to relink a USDA reference that doesn't fit. Searching for each on every
look took seconds. The answer depends only on the name, the loaded USDA release
and the guessing rules, so it is kept in ``cleanup_guess`` under that catalog
version and searched again only for a name not yet kept or kept at another
version. The worker fills them while idle, so even the first look is quick.
"""

from __future__ import annotations

import functools
import hashlib
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories, names, standard_match, usda_fit
from app.models import CleanupGuess, FdcRelease
from app.services import ingredient_category, usda

GuessKind = Literal["category", "usda_link"]


@dataclass(frozen=True)
class Kept:
    category: str | None
    fdc_id: int | None


@functools.cache
def rules_version() -> str:
    """A digest of the modules whose rules shape a guess: new rules guess again."""
    digest = hashlib.sha1(usedforsecurity=False)
    modules = (usda_fit, usda, categories, names, standard_match, ingredient_category)
    for module in (*modules, sys.modules[__name__]):
        digest.update(Path(module.__file__ or "").read_bytes())
    return digest.hexdigest()[:12]


async def catalog_version(db: AsyncSession) -> str:
    """The guessing rules and the latest USDA release loaded."""
    release = await db.scalar(
        select(FdcRelease.id).order_by(FdcRelease.imported_at.desc()).limit(1)
    )
    return f"{rules_version()}:{release or 'none'}"


async def guesses(db: AsyncSession, kind: GuessKind, wanted: Iterable[str]) -> dict[str, Kept]:
    """The guess for each name, kept or searched now and kept."""
    want = set(wanted)
    if not want:
        return {}
    version = await catalog_version(db)
    rows = await db.execute(
        select(CleanupGuess.name, CleanupGuess.category, CleanupGuess.fdc_id).where(
            CleanupGuess.kind == kind,
            CleanupGuess.version == version,
            CleanupGuess.name.in_(want),
        )
    )
    kept = {name: Kept(category, fdc_id) for name, category, fdc_id in rows}
    missing = sorted(want - kept.keys())
    if not missing:
        return kept
    for name in missing:
        kept[name] = await _search(db, kind, name)
    stmt = insert(CleanupGuess).values(
        [
            {
                "kind": kind,
                "name": name,
                "version": version,
                "category": kept[name].category,
                "fdc_id": kept[name].fdc_id,
            }
            for name in missing
        ]
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["kind", "name"],
            set_={
                "version": stmt.excluded.version,
                "category": stmt.excluded.category,
                "fdc_id": stmt.excluded.fdc_id,
                "computed_at": func.now(),
            },
        )
    )
    # Guesses of an older release or older rules are never asked for again.
    await db.execute(delete(CleanupGuess).where(CleanupGuess.version != version))
    await db.commit()
    return kept


async def _search(db: AsyncSession, kind: GuessKind, name: str) -> Kept:
    if kind == "category":
        return Kept(await ingredient_category.from_usda(db, name), None)
    found = await usda.candidates(db, name, limit=5, fit_name=name)
    better = next((f for f in found if usda_fit.fits(name, f.description).verdict == "fits"), None)
    return Kept(None, better.fdc_id if better else None)
