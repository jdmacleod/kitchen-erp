"""The repository indexer (07, 3A): scan the mount, keep ``recipe`` rows in step with it.

One scan lists ``*.cook`` files under ``RECIPES_PATH``, skips those modified
within the settle window, hashes the rest, parses the ones whose hash changed,
asks dulwich for ``HEAD`` and for renames since the last indexed commit, hands
all of it to the pure planner in ``app.recipes.index`` and applies the plan.
Nothing here writes under the mount.

Titles, servings, front matter and the ``recipe_ingredient`` rows come from the
Cooklang parser (3B). A file that stops parsing keeps its last good rows and is
marked ``parse_error`` with the new hash (criterion 4), so it is not parsed
again until it changes. Every row is written ``unmatched`` here with
``negligible`` decided from the quantity alone (none, or text such as "a
handful"); the scan then hands every unmatched row to the 3C lookup
(``recipe_resolution.resolve_unmatched``), which settles names, the negligible
list and ignored names in the same transaction.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.names import normalize_name
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.models import Ingredient, Recipe, RecipeIngredient
from app.recipes import index
from app.recipes.cooklang import (
    ParseError,
    QuantityNumber,
    QuantityRange,
    QuantityText,
    parse,
)
from app.recipes.cooklang import Recipe as ParsedRecipe
from app.recipes.hashing import blob_sha1
from app.recipes.repo import RepoView, open_repo
from app.schemas.recipes import (
    MountState,
    RecipeIngredientOut,
    RecipeListItem,
    RecipeOut,
    RecipesStatus,
    RelinkProposal,
    ScanOut,
    StatusCounts,
)
from app.services import recipe_cost_views, recipe_resolution

Parsed = ParsedRecipe | ParseError

# When this process last finished a scan. The worker and the api scan in
# separate processes, so status() also looks at the rows themselves.
_last_scan: dict[str, datetime | None] = {"at": None}


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


# --- applying the plan ----------------------------------------------------------------


def _mark_present(row: Recipe, repo: RepoView, now: datetime) -> None:
    """What every file on disk gets, re-indexed or not: presence, HEAD, dirtiness."""
    row.last_seen_at = now
    row.head_commit = repo.head_commit
    row.dirty = repo.blob_id(row.path) != row.content_hash
    if row.status == "missing":
        row.status = "ok"
    row.relink_candidate_id = None
    row.relink_reason = None


def _apply_good_parse(row: Recipe, parsed: ParsedRecipe) -> None:
    row.title = _title_of(parsed, row.path)
    row.servings = parsed.servings
    row.servings_text = parsed.servings_text
    row.front_matter = dict(parsed.front_matter)
    row.status = "ok"
    row.parse_error_message = None


async def _reindex(
    db: AsyncSession, row: Recipe, content_hash: str, parsed: Parsed, now: datetime
) -> bool:
    """The file's content changed: replace what was derived from it. True when it parsed.

    On a parse error (criterion 4) the title, front matter, servings and the
    ``recipe_ingredient`` rows of the last good parse stay, and the new hash is
    stored so the file is not parsed again until it changes.
    """
    row.content_hash = content_hash
    row.last_indexed_at = now
    if isinstance(parsed, ParseError):
        row.status = "parse_error"
        row.parse_error_message = _error_message(parsed)
        return False
    _apply_good_parse(row, parsed)
    await db.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == row.id))
    for line in _ingredient_rows(parsed):
        line.recipe_id = row.id
        db.add(line)
    return True


def _create(create: index.Create, parsed: Parsed, repo: RepoView, now: datetime) -> Recipe:
    row = Recipe(
        path=create.path,
        title=_title_of(parsed, create.path),
        content_hash=create.content_hash,
        head_commit=repo.head_commit,
        dirty=repo.blob_id(create.path) != create.content_hash,
        status="ok",
        last_indexed_at=now,
        last_seen_at=now,
    )
    if isinstance(parsed, ParseError):
        row.status = "parse_error"
        row.parse_error_message = _error_message(parsed)
    else:
        _apply_good_parse(row, parsed)
        row.ingredients = _ingredient_rows(parsed)
    return row


async def scan(db: AsyncSession, settings: Settings | None = None) -> ScanOut:
    """Run one scan and commit what it changed. Safe to run from the worker or inline."""
    settings = settings or get_settings()
    now = datetime.now(UTC)
    mount = inspect_mount(settings)
    if mount.state != "mounted":
        _last_scan["at"] = now
        return ScanOut(
            mounted=False,
            scanned_at=now,
            files=0,
            settling=0,
            created=0,
            updated=0,
            moved=0,
            missing=0,
            parse_errors=0,
            proposals=0,
        )

    rows = list((await db.execute(select(Recipe))).scalars())
    known = {row.path: row.content_hash for row in rows}
    files, parsed = _list_files(mount, time.time(), settings.recipes_settle_seconds, known)
    # Only a row whose path is gone can be a relink's origin, and only its
    # ingredient names take part in resemblance (3A, step 3).
    on_disk = {f.path for f in files}
    names = await _ingredient_names(db, [row.id for row in rows if row.path not in on_disk])
    parse_errors = 0
    with open_repo(mount.root) as repo:
        renames: dict[str, str] = {}
        previous = _last_indexed_head(rows)
        if repo.head_commit and previous and previous != repo.head_commit:
            renames = repo.renames(previous, repo.head_commit)
        plan = index.plan(
            _stored(rows, names),
            files,
            now=time.time(),
            settle_seconds=settings.recipes_settle_seconds,
            renames=renames,
        )
        by_id = {row.id: row for row in rows}
        created: dict[str, Recipe] = {}
        relinks: list[tuple[uuid.UUID, str, str]] = []

        for action in plan.actions:
            match action:
                case index.Seen(recipe_id=rid):
                    _mark_present(by_id[rid], repo, now)
                case index.Update(recipe_id=rid, path=path, content_hash=h):
                    row = by_id[rid]
                    if not await _reindex(db, row, h, parsed[path], now):
                        parse_errors += 1
                    _mark_present(row, repo, now)
                case index.Move(recipe_id=rid, path=new_path, content_hash=h):
                    row = by_id[rid]
                    row.path = new_path
                    if row.content_hash != h:
                        if not await _reindex(db, row, h, parsed[new_path], now):
                            parse_errors += 1
                    elif not _has_front_matter_title(row):
                        row.title = _title_for(new_path)  # the stem stood in; it moved
                    _mark_present(row, repo, now)
                case index.Missing(recipe_id=rid):
                    by_id[rid].status = "missing"
        # A new path never carries a row, and a moved row never takes a path that
        # has one (the planner treats a present path as the same recipe), so one
        # flush orders nothing wrongly.
        for create in plan.of(index.Create):
            row = _create(create, parsed[create.path], repo, now)
            if row.status == "parse_error":
                parse_errors += 1
            db.add(row)
            created[create.path] = row
            if create.relink_of is not None and create.relink_reason is not None:
                relinks.append((create.relink_of, create.path, create.relink_reason))
        await db.flush()
        for origin, path, why in relinks:
            by_id[origin].relink_candidate_id = created[path].id
            by_id[origin].relink_reason = why
    # Rows just rebuilt and rows that waited, in one pass (3C, criterion 14).
    await recipe_resolution.resolve_unmatched(db, settings)
    await db.commit()
    _last_scan["at"] = now
    return ScanOut(
        mounted=True,
        scanned_at=now,
        files=len(files),
        settling=len(plan.of(index.Settling)),
        created=len(plan.of(index.Create)),
        updated=len(plan.of(index.Update)),
        moved=len(plan.of(index.Move)),
        missing=len(plan.of(index.Missing)),
        parse_errors=parse_errors,
        proposals=len(relinks),
    )


async def status(db: AsyncSession, settings: Settings | None = None) -> RecipesStatus:
    settings = settings or get_settings()
    mount = inspect_mount(settings)
    head: str | None = None
    if mount.state == "mounted":
        with open_repo(mount.root) as repo:
            head = repo.head_commit
    counted = (await db.execute(select(Recipe.status, func.count()).group_by(Recipe.status))).all()
    counts = StatusCounts(**dict(counted))
    newest = (await db.execute(select(func.max(Recipe.last_seen_at)))).scalar_one_or_none()
    candidates = [t for t in (_last_scan["at"], newest) if t is not None]
    return RecipesStatus(
        mount=mount.state,
        mounted=mount.state == "mounted",
        head_commit=head,
        counts=counts,
        total=counts.ok + counts.parse_error + counts.missing,
        last_scan_at=max(candidates) if candidates else None,
    )


async def list_recipes(
    db: AsyncSession,
    *,
    status: str | None = None,
    dirty: bool | None = None,
    q: str | None = None,
    completeness: str | None = None,
) -> list[RecipeListItem]:
    """Every recipe with its default ``latest`` cost when its current content has one (3D).

    Ordered by title, then path (UI-7.3). ``q`` matches the title or the path, case
    folded. ``completeness`` keeps recipes whose current cost has every line priced
    (``complete``) or not (``incomplete``: some line unpriced, or no snapshot yet).
    """
    stmt = select(Recipe).order_by(func.lower(Recipe.title), Recipe.path)
    if status is not None:
        stmt = stmt.where(Recipe.status == status)
    if dirty is not None:
        stmt = stmt.where(Recipe.dirty.is_(dirty))
    if q:
        needle = f"%{_escape_like(q.strip())}%"
        stmt = stmt.where(
            Recipe.title.ilike(needle, escape="\\") | Recipe.path.ilike(needle, escape="\\")
        )
    rows = (await db.execute(stmt)).scalars().all()
    costs = await recipe_cost_views.summaries(db, list(rows))
    items = [
        RecipeListItem.model_validate(row).model_copy(update={"cost": costs.get(row.id)})
        for row in rows
    ]
    if completeness == "complete":
        items = [
            i for i in items if i.cost is not None and i.cost.lines_priced == i.cost.lines_total
        ]
    elif completeness == "incomplete":
        items = [i for i in items if i.cost is None or i.cost.lines_priced < i.cost.lines_total]
    return items


def _escape_like(text: str) -> str:
    """Escape the LIKE metacharacters so a typed ``%`` or ``_`` matches itself."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_recipe(db: AsyncSession, recipe_id: uuid.UUID) -> Recipe:
    row = await db.get(Recipe, recipe_id)
    if row is None:
        raise ApiError(404, "not_found", "No such recipe.")
    return row


async def recipe_out(db: AsyncSession, row: Recipe) -> RecipeOut:
    relink: RelinkProposal | None = None
    if row.relink_candidate_id is not None:
        target = await db.get(Recipe, row.relink_candidate_id)
        if target is not None:
            relink = RelinkProposal(
                target_id=target.id,
                path=target.path,
                title=target.title,
                reason=row.relink_reason or "",
            )
    lines = (
        (
            await db.execute(
                select(RecipeIngredient)
                .where(RecipeIngredient.recipe_id == row.id)
                .order_by(RecipeIngredient.seq)
            )
        )
        .scalars()
        .all()
    )
    ingredient_ids = {line.ingredient_id for line in lines if line.ingredient_id is not None}
    names: dict[uuid.UUID, str] = {}
    if ingredient_ids:
        found = await db.execute(
            select(Ingredient.id, Ingredient.name).where(Ingredient.id.in_(ingredient_ids))
        )
        names = dict(found.all())
    # Column by column: the row's `ingredients` and `pins` relationships are not
    # loaded, and reading them here would lazy-load outside the session's greenlet.
    columns = {
        name: getattr(row, name)
        for name in RecipeOut.model_fields
        if name not in ("relink", "ingredients", "pins")
    }
    return RecipeOut(
        **columns,
        relink=relink,
        ingredients=[
            RecipeIngredientOut.model_validate(line).model_copy(
                update={"ingredient_name": names.get(line.ingredient_id)}
            )
            for line in lines
        ],
        pins=await recipe_resolution.pins_out(db, row.id),
    )


async def relink(db: AsyncSession, recipe_id: uuid.UUID, target_id: uuid.UUID) -> Recipe:
    """Confirm that a missing recipe became ``target``: the old row takes the new
    file, its parse and its ingredient rows, and the new row goes, so pins and
    history stay with the recipe."""
    row = await get_recipe(db, recipe_id)
    if row.status != "missing":
        raise ApiError(409, "not_missing", "Only a missing recipe can be relinked.")
    if target_id == recipe_id:
        raise ApiError(409, "same_recipe", "A recipe cannot be relinked to itself.")
    target = await db.get(Recipe, target_id)
    if target is None:
        raise ApiError(404, "not_found", "No such target recipe.")
    if target.status == "missing":
        raise ApiError(409, "target_missing", "The target recipe has no file either.")
    carried = {
        name: getattr(target, name)
        for name in (
            "path",
            "title",
            "servings",
            "servings_text",
            "content_hash",
            "head_commit",
            "dirty",
            "parse_error_message",
            "front_matter",
            "last_indexed_at",
            "last_seen_at",
        )
    }
    # The target's ingredient rows become the recipe's and the rows of its last
    # good parse at the old path go. A target that never parsed has no rows, and
    # then the recipe keeps its last good ones (criterion 4), with the target's
    # parse error on it.
    target_lines = await db.scalar(
        select(func.count())
        .select_from(RecipeIngredient)
        .where(RecipeIngredient.recipe_id == target.id)
    )
    if target_lines:
        await db.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == row.id))
        await db.execute(
            update(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == target.id)
            .values(recipe_id=row.id)
        )
    await db.delete(target)
    await db.flush()  # the path must be free before the old row takes it
    for name, value in carried.items():
        setattr(row, name, value)
    row.status = "parse_error" if carried["parse_error_message"] else "ok"
    row.relink_candidate_id = None
    row.relink_reason = None
    await db.commit()
    await db.refresh(row)
    return row


async def delete_recipe(db: AsyncSession, recipe_id: uuid.UUID) -> None:
    """Remove a recipe explicitly; only one whose file is gone (3A)."""
    row = await get_recipe(db, recipe_id)
    if row.status != "missing":
        raise ApiError(409, "not_missing", "Only a missing recipe can be removed.")
    await db.delete(row)
    await db.commit()
