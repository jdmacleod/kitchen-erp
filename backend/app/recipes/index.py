"""The scan planner (07, 3A): stored rows plus the on-disk listing give a list of actions.

Pure: it reads nothing and writes nothing. ``services.recipes`` lists the mount,
hashes files, asks dulwich about ``HEAD`` and renames, and hands everything here;
the plan that comes back is applied to the database verbatim. Every on-disk path
lands in exactly one of ``Settling``, ``Seen``, ``Update``, ``Move`` or ``Create``;
``Missing`` names stored rows whose file is gone.

Identity across renames is carried in three steps, in this order:

1. a path that disappeared while a new path appeared with the same content hash
   is a pure move: the row is repointed (``Move`` with ``reason="hash"``);
2. git's rename detection between the last indexed commit and ``HEAD`` repoints
   rows for committed moves combined with edits (``Move`` with ``reason="git"``);
3. for an uncommitted move with an edit, a new file whose title resembles a
   missing recipe's, or shares at least two thirds of its ingredient names, is
   created with a relink proposal pointing at that recipe; a person confirms it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fractions import Fraction

# A new file whose normalized title matches a missing recipe's at least this
# closely is proposed as its relink target.
TITLE_SIMILARITY = 0.75
# ... or whose ingredient names cover at least this share of the missing recipe's.
INGREDIENT_OVERLAP = Fraction(2, 3)


@dataclass(frozen=True)
class StoredRecipe:
    """What the planner needs to know about a ``recipe`` row."""

    id: uuid.UUID
    path: str
    content_hash: str
    status: str  # ok, parse_error, missing
    title: str = ""
    ingredients: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DiskFile:
    """A ``.cook`` file as listed; ``content_hash`` is None while it is still settling."""

    path: str
    mtime: float
    content_hash: str | None
    title: str = ""
    ingredients: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Settling:
    """Modified within the settle window: left alone until the next scan."""

    path: str


@dataclass(frozen=True)
class Seen:
    """On disk with the stored hash: nothing to re-index, only its presence to note."""

    recipe_id: uuid.UUID
    path: str


@dataclass(frozen=True)
class Update:
    """Same path, different content."""

    recipe_id: uuid.UUID
    path: str
    content_hash: str


@dataclass(frozen=True)
class Move:
    """The row at ``old_path`` now lives at ``path``; the hash changes with a git rename."""

    recipe_id: uuid.UUID
    old_path: str
    path: str
    content_hash: str
    reason: str  # "hash" (pure move) or "git" (rename detection)


@dataclass(frozen=True)
class Create:
    """A path with no row. ``relink_of`` proposes it as a missing recipe's new home."""

    path: str
    content_hash: str
    relink_of: uuid.UUID | None = None
    relink_reason: str | None = None


@dataclass(frozen=True)
class Missing:
    """A stored row whose file is gone and that is not already marked missing."""

    recipe_id: uuid.UUID
    path: str


Action = Settling | Seen | Update | Move | Create | Missing


@dataclass(frozen=True)
class Plan:
    actions: tuple[Action, ...] = field(default_factory=tuple)

    def of(self, kind: type) -> list:
        return [a for a in self.actions if isinstance(a, kind)]


_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    return " ".join(_NON_WORD.sub(" ", title.lower()).split())


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def ingredient_overlap(missing: frozenset[str], candidate: frozenset[str]) -> Fraction:
    """The share of the missing recipe's ingredient names the candidate also has."""
    if not missing:
        return Fraction(0)
    return Fraction(len(missing & candidate), len(missing))


def resemblance(missing: StoredRecipe, candidate: DiskFile) -> tuple[float, str] | None:
    """A score and a reason when the candidate resembles the missing recipe, else None."""
    similarity = title_similarity(missing.title, candidate.title)
    overlap = ingredient_overlap(missing.ingredients, candidate.ingredients)
    if similarity >= TITLE_SIMILARITY:
        return similarity, f"title similarity {similarity:.2f}"
    if overlap >= INGREDIENT_OVERLAP:
        return float(overlap), f"{overlap.numerator}/{overlap.denominator} ingredients in common"
    return None


def plan(
    stored: list[StoredRecipe] | tuple[StoredRecipe, ...],
    on_disk: list[DiskFile] | tuple[DiskFile, ...],
    *,
    now: float,
    settle_seconds: float,
    renames: dict[str, str] | None = None,
) -> Plan:
    """Decide what the scan does. Deterministic: the same inputs give the same plan.

    ``renames`` maps old path to new path as git's rename detection reported them
    between the last indexed commit and ``HEAD``; empty when ``HEAD`` did not move.
    """
    renames = renames or {}
    by_path = {row.path: row for row in sorted(stored, key=lambda r: r.path)}
    files = sorted(on_disk, key=lambda f: f.path)
    actions: list[Action] = []

    settling: set[str] = set()
    present: dict[str, DiskFile] = {}
    for f in files:
        if f.content_hash is None or now - f.mtime < settle_seconds:
            settling.add(f.path)
            actions.append(Settling(f.path))
        else:
            present[f.path] = f

    # Paths both stored and on disk.
    appeared: dict[str, DiskFile] = {}
    for path, f in present.items():
        row = by_path.get(path)
        if row is None:
            appeared[path] = f
        elif row.content_hash == f.content_hash:
            actions.append(Seen(row.id, path))
        else:
            actions.append(Update(row.id, path, f.content_hash))

    # Rows whose file is gone: candidates for a move, including rows marked
    # missing on an earlier scan (a move can straddle two scans). A row whose
    # path is still settling is neither gone nor seen.
    gone: dict[str, StoredRecipe] = {
        path: row for path, row in by_path.items() if path not in present and path not in settling
    }

    # 1. Pure moves: same hash, new path.
    by_hash: dict[str, list[StoredRecipe]] = {}
    for row in gone.values():
        by_hash.setdefault(row.content_hash, []).append(row)
    for path in sorted(appeared):
        f = appeared[path]
        candidates = by_hash.get(f.content_hash or "")
        if not candidates:
            continue
        row = candidates.pop(0)
        actions.append(Move(row.id, row.path, path, f.content_hash, "hash"))
        del appeared[path]
        del gone[row.path]

    # 2. Committed moves with edits, as git sees them.
    for old_path in sorted(renames):
        new_path = renames[old_path]
        row = gone.get(old_path)
        f = appeared.get(new_path)
        if row is None or f is None or f.content_hash is None:
            continue
        actions.append(Move(row.id, row.path, new_path, f.content_hash, "git"))
        del appeared[new_path]
        del gone[old_path]

    # 3. Uncommitted moves with edits: proposals, best resemblance first, one
    #    proposal per missing recipe and one per new file.
    proposals: dict[str, tuple[uuid.UUID, str]] = {}
    scored: list[tuple[float, str, str, uuid.UUID, str]] = []
    for old_path, row in gone.items():
        for new_path, f in appeared.items():
            found = resemblance(row, f)
            if found is not None:
                score, reason = found
                scored.append((-score, old_path, new_path, row.id, reason))
    taken: set[uuid.UUID] = set()
    for _neg, _old_path, new_path, recipe_id, reason in sorted(scored):
        if recipe_id in taken or new_path in proposals:
            continue
        taken.add(recipe_id)
        proposals[new_path] = (recipe_id, reason)

    for path in sorted(appeared):
        f = appeared[path]
        assert f.content_hash is not None
        relink = proposals.get(path)
        actions.append(
            Create(
                path,
                f.content_hash,
                relink_of=relink[0] if relink else None,
                relink_reason=relink[1] if relink else None,
            )
        )

    for path in sorted(gone):
        row = gone[path]
        if row.status != "missing":
            actions.append(Missing(row.id, path))

    return Plan(tuple(actions))
