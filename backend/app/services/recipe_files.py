"""The recipe mount and what the indexer reads from it (07, 3A, 3B).

The mount's state and its ``.cook`` files, each file's hash and parse (a file
still settling is listed unhashed), and the ``recipe_ingredient`` rows a good
parse gives. ``services/recipes`` runs the scan that uses them.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.names import normalize_name
from app.core.config import Settings, get_settings
from app.models import Recipe, RecipeIngredient
from app.recipes import index
from app.recipes.cooklang import ParseError, QuantityNumber, QuantityRange, QuantityText, parse
from app.recipes.cooklang import Recipe as ParsedRecipe
from app.recipes.hashing import blob_sha1
from app.schemas.recipes import MountState

Parsed = ParsedRecipe | ParseError


@dataclass(frozen=True)
class Mount:
    root: Path
    state: MountState
    files: list[Path]


def inspect_mount(settings: Settings | None = None) -> Mount:
    """The mount's state and its ``.cook`` files, never descending into ``.git``.

    A ``.git`` entry counts as a repository (package 2, 2026-10-09): a repository
    with no ``.cook`` files is mounted and has no recipes, so the scan runs and
    every indexed recipe becomes missing. "No repository" is a directory that is
    missing, or that holds neither ``.cook`` files nor ``.git``: ``empty`` when it
    holds nothing at all, ``no_cook_files`` when it holds other things.
    """
    settings = settings or get_settings()
    root = Path(settings.recipes_path)
    if not root.is_dir():
        return Mount(root, "missing", [])
    files: list[Path] = []
    any_entry = False
    for dirpath, dirnames, filenames in os.walk(root):
        any_entry = any_entry or bool(filenames) or bool(dirnames)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith(".cook") and not name.startswith("."):
                files.append(Path(dirpath) / name)
    if files or (root / ".git").exists():
        return Mount(root, "mounted", files)
    return Mount(root, "empty" if not any_entry else "no_cook_files", [])


# --- what the parser gives a row ----------------------------------------------------


def _title_for(path: str) -> str:
    """The file's stem, humanized: the title when the front matter has none."""
    stem = Path(path).stem
    text = " ".join(stem.replace("-", " ").replace("_", " ").split()) or stem
    return text[:1].upper() + text[1:]


def _title_of(parsed: Parsed | None, path: str) -> str:
    if isinstance(parsed, ParsedRecipe) and parsed.title:
        return parsed.title
    return _title_for(path)


def _has_front_matter_title(row: Recipe) -> bool:
    title = (row.front_matter or {}).get("title")
    return isinstance(title, str) and bool(title.strip())


def _error_message(error: ParseError) -> str:
    return f"{error.message} (line {error.line}, column {error.column})"


def _parse_bytes(data: bytes) -> Parsed:
    """Parse a file's bytes; a file that is not UTF-8 text is a parse error too."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = data.count(b"\n", 0, exc.start) + 1
        column = exc.start - (data.rfind(b"\n", 0, exc.start) + 1) + 1
        return ParseError("file is not UTF-8 text", line, column)
    return parse(text)


def _ingredient_rows(parsed: ParsedRecipe) -> list[RecipeIngredient]:
    """One row per ingredient reference in document order, ``seq`` from 1."""
    rows: list[RecipeIngredient] = []
    for seq, (section, ref) in enumerate(parsed.ingredient_lines, start=1):
        quantity = ref.quantity
        qty = qty_high = None
        qty_text = None
        if isinstance(quantity, QuantityNumber):
            qty = quantity.value
        elif isinstance(quantity, QuantityRange):
            qty, qty_high = quantity.low, quantity.high
        elif isinstance(quantity, QuantityText):
            qty_text = quantity.text
        rows.append(
            RecipeIngredient(
                seq=seq,
                section=section,
                raw_name=ref.raw_name,
                name_norm=normalize_name(ref.raw_name),
                qty_kind=quantity.kind,
                qty=qty,
                qty_high=qty_high,
                qty_text=qty_text,
                unit_text=ref.unit_text,
                # The 1B parser's code, or NULL when it reported a failure (a
                # measure label such as "clove" stays in unit_text for 3C).
                unit=ref.unit if isinstance(ref.unit, str) else None,
                note=ref.note,
                # The negligible-name list is applied by the 3C lookup after the scan.
                negligible=quantity.kind in ("none", "text"),
                resolution="unmatched",
                ingredient_id=None,
                yield_mode="auto",
            )
        )
    return rows


def _names(parsed: Parsed | None) -> frozenset[str]:
    """The normalized ingredient names the planner compares; empty for a parse error."""
    if not isinstance(parsed, ParsedRecipe):
        return frozenset()
    return frozenset(
        name for name in (normalize_name(ref.raw_name) for ref in parsed.ingredients) if name
    )


# --- the listing ----------------------------------------------------------------------


def _list_files(
    mount: Mount, now: float, settle_seconds: float, known: dict[str, str]
) -> tuple[list[index.DiskFile], dict[str, Parsed]]:
    """Each file with its hash, except those still settling, which are listed unhashed.

    A file is read only once its modification time is outside the settle window,
    so a half-written file is never hashed. A file whose hash differs from the
    row stored at its path (``known``) is parsed, so that every path the planner
    may create, update or move has a parse result; a file the index already
    holds at that hash is not.
    """
    out: list[index.DiskFile] = []
    parsed: dict[str, Parsed] = {}
    for file in mount.files:
        rel = file.relative_to(mount.root).as_posix()
        try:
            mtime = file.stat().st_mtime
        except FileNotFoundError:
            continue  # gone between listing and stat: next scan sees whatever is true
        content_hash: str | None = None
        if now - mtime >= settle_seconds:
            try:
                data = file.read_bytes()
            except FileNotFoundError:
                continue
            content_hash = blob_sha1(data)
            if known.get(rel) != content_hash:
                parsed[rel] = _parse_bytes(data)
        result = parsed.get(rel)
        out.append(
            index.DiskFile(
                rel,
                mtime,
                content_hash,
                title=_title_of(result, rel) if result is not None else "",
                ingredients=_names(result),
            )
        )
    return out, parsed


def _stored(rows: list[Recipe], names: dict[uuid.UUID, frozenset[str]]) -> list[index.StoredRecipe]:
    return [
        index.StoredRecipe(
            id=row.id,
            path=row.path,
            content_hash=row.content_hash,
            status=row.status,
            title=row.title,
            ingredients=names.get(row.id, frozenset()),
        )
        for row in rows
    ]


async def _ingredient_names(
    db: AsyncSession, recipe_ids: list[uuid.UUID]
) -> dict[uuid.UUID, frozenset[str]]:
    """The normalized ingredient names of the last good parse, per recipe."""
    if not recipe_ids:
        return {}
    found = await db.execute(
        select(RecipeIngredient.recipe_id, RecipeIngredient.name_norm).where(
            RecipeIngredient.recipe_id.in_(recipe_ids)
        )
    )
    names: dict[uuid.UUID, set[str]] = {}
    for recipe_id, name in found:
        if name:
            names.setdefault(recipe_id, set()).add(name)
    return {recipe_id: frozenset(found_names) for recipe_id, found_names in names.items()}


def _last_indexed_head(rows: list[Recipe]) -> str | None:
    """The commit the most recent scan indexed against, from the rows it touched."""
    newest = max((r for r in rows if r.head_commit), key=lambda r: r.last_seen_at, default=None)
    return newest.head_commit if newest else None
