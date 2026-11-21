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
list and ignored names in the same transaction. Cost snapshots (3D) follow in
that transaction too: a recipe whose content changed, or whose waiting lines
the lookup settled, is recomputed, and one committed unchanged only loses its
provisional flag (``recipe_cost_triggers``).

The mount's files and the rows a parse gives live in ``services/recipe_files``;
the reads (list, detail, relink, delete) in ``services/recipe_reads``, re-exported
here for the router and the tests.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import Recipe, RecipeIngredient
from app.recipes import index
from app.recipes.cooklang import ParseError
from app.recipes.cooklang import Recipe as ParsedRecipe
from app.recipes.repo import RepoChanging, RepoView, open_repo
from app.schemas.recipes import MissingRecipeRef, RecipesStatus, ScanOut, StatusCounts
from app.services import recipe_cost_triggers, recipe_pages, recipe_refs, recipe_resolution
from app.services.recipe_files import (
    Parsed,
    _error_message,
    _has_front_matter_title,
    _ingredient_names,
    _ingredient_rows,
    _last_indexed_head,
    _list_files,
    _stored,
    _title_for,
    _title_of,
    inspect_mount,
)
from app.services.recipe_reads import delete_recipe as delete_recipe
from app.services.recipe_reads import get_recipe as get_recipe
from app.services.recipe_reads import list_recipes as list_recipes
from app.services.recipe_reads import recipe_out as recipe_out
from app.services.recipe_reads import relink as relink

log = get_logger(__name__)

# When this process last finished a scan. The worker and the api scan in
# separate processes, so status() also looks at the rows themselves.
_last_scan: dict[str, datetime | None] = {"at": None}
# The advisory-lock key that serializes scans (one per database; arbitrary but fixed).
SCAN_LOCK_KEY = 0x6B65_7270_7263  # "kerprc"


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
    row.body = recipe_pages.serialize_body(parsed)  # the steps, for the rendered recipe (0048)
    row.makes_qty = parsed.makes_qty  # front matter `yield:` (0071, #526)
    row.makes_unit = parsed.makes_unit
    row.makes_text = parsed.makes_text
    row.status = "ok"
    row.parse_error_message = None


async def _reindex(
    db: AsyncSession,
    row: Recipe,
    content_hash: str,
    parsed: Parsed,
    now: datetime,
    *,
    keep_choices: bool = False,
) -> bool:
    """The file's content changed: replace what was derived from it. True when it parsed.

    On a parse error (criterion 4) the title, front matter, servings and the
    ``recipe_ingredient`` rows of the last good parse stay, and the new hash is
    stored so the file is not parsed again until it changes.

    ``keep_choices`` is for a reparse of unchanged content: a line at the same
    place with the same name keeps its ingredient, resolution and yield mode, so
    re-reading a file never undoes what a person chose for its lines.
    """
    row.content_hash = content_hash
    row.last_indexed_at = now
    if isinstance(parsed, ParseError):
        row.status = "parse_error"
        row.parse_error_message = _error_message(parsed)
        return False
    _apply_good_parse(row, parsed)
    kept: dict[tuple[int, str], tuple[uuid.UUID | None, str, str]] = {}
    if keep_choices:
        found = await db.execute(
            select(
                RecipeIngredient.seq,
                RecipeIngredient.name_norm,
                RecipeIngredient.ingredient_id,
                RecipeIngredient.resolution,
                RecipeIngredient.yield_mode,
            ).where(RecipeIngredient.recipe_id == row.id)
        )
        kept = {(seq, name): (ing, res, ym) for seq, name, ing, res, ym in found.tuples()}
    await db.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == row.id))
    for line in _ingredient_rows(parsed):
        line.recipe_id = row.id
        choice = kept.get((line.seq, line.name_norm))
        # A line that became, or stopped being, a recipe reference takes no old choice.
        if choice is not None and (choice[1] == "recipe") == (line.resolution == "recipe"):
            line.ingredient_id, line.resolution, line.yield_mode = choice
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


def _deferred(now: datetime, files: int) -> ScanOut:
    """A scan that changed nothing because the repository was mid-write: every file waits."""
    return ScanOut(
        mounted=True,
        scanned_at=now,
        files=files,
        settling=files,
        created=0,
        updated=0,
        moved=0,
        missing=0,
        parse_errors=0,
        proposals=0,
    )


async def scan(  # noqa: C901, PLR0912, PLR0915
    db: AsyncSession, settings: Settings | None = None, *, reparse: bool = False
) -> ScanOut:
    """Run one scan and commit what it changed. Safe to run from the worker or inline.

    ``reparse`` re-reads every settled file whose content did not change, for a
    parser that now reads files differently (`kerp recipes rescan --reparse`).
    """
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

    # One scan at a time. The worker's periodic scan and the Rescan button (or two
    # browser tabs) otherwise read the same empty index and both insert the same
    # paths, and the loser answers 500 with an IntegrityError. The lock lives for
    # this transaction, so the waiter starts after the first scan's commit and
    # reads its rows (package 10, found by the browser suite).
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCAN_LOCK_KEY})
    rows = list((await db.execute(select(Recipe))).scalars())
    known = {row.path: row.content_hash for row in rows}
    files, parsed = _list_files(
        mount, time.time(), settings.recipes_settle_seconds, {} if reparse else known
    )
    # Only a row whose path is gone can be a relink's origin, and only its
    # ingredient names take part in resemblance (3A, step 3).
    on_disk = {f.path for f in files}
    names = await _ingredient_names(db, [row.id for row in rows if row.path not in on_disk])
    parse_errors = 0
    try:
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
            # What the scan means for cost snapshots (3D): a changed hash is a
            # recompute; an unchanged hash whose dirty flag flipped only moves the
            # provisional flag (criterion 24).
            changed: set[uuid.UUID] = set()
            committed: list[Recipe] = []
            uncommitted: list[Recipe] = []

            def _note_dirty(row: Recipe, was_dirty: bool) -> None:
                if was_dirty and not row.dirty:
                    committed.append(row)
                elif row.dirty and not was_dirty:
                    uncommitted.append(row)

            for action in plan.actions:
                match action:
                    case index.Seen(recipe_id=rid, path=path) if reparse:
                        row = by_id[rid]
                        if not await _reindex(
                            db, row, row.content_hash, parsed[path], now, keep_choices=True
                        ):
                            parse_errors += 1
                        _mark_present(row, repo, now)
                        changed.add(rid)
                    case index.Seen(recipe_id=rid):
                        row = by_id[rid]
                        was_dirty = row.dirty
                        _mark_present(row, repo, now)
                        _note_dirty(row, was_dirty)
                    case index.Update(recipe_id=rid, path=path, content_hash=h):
                        row = by_id[rid]
                        if not await _reindex(db, row, h, parsed[path], now):
                            parse_errors += 1
                        _mark_present(row, repo, now)
                        changed.add(rid)
                    case index.Move(recipe_id=rid, path=new_path, content_hash=h):
                        row = by_id[rid]
                        row.path = new_path
                        was_dirty = row.dirty
                        if row.content_hash != h or reparse:
                            same = row.content_hash == h
                            if not await _reindex(
                                db, row, h, parsed[new_path], now, keep_choices=same
                            ):
                                parse_errors += 1
                            changed.add(rid)
                        elif not _has_front_matter_title(row):
                            row.title = _title_for(new_path)  # the stem stood in; it moved
                        _mark_present(row, repo, now)
                        if rid not in changed:
                            _note_dirty(row, was_dirty)
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
            changed.update(row.id for row in created.values())
            for origin, path, why in relinks:
                if recipe_pages.is_dismissed(by_id[origin], path):
                    continue  # "Not the same" was said about this file (package 8)
                by_id[origin].relink_candidate_id = created[path].id
                by_id[origin].relink_reason = why
    except RepoChanging:
        # git is mid-write, or the mount has not caught up with it: nothing this
        # scan did is kept, and the next one indexes the settled repository (#477).
        await db.rollback()
        log.info("recipe repository is changing; scan deferred")
        return _deferred(now, len(files))
    # Recipe references, every one, so a new or moved target is seen (#526).
    links = await recipe_refs.link_recipe_refs(db)
    changed |= links.changed
    # Rows just rebuilt and rows that waited, in one pass (3C, criterion 14).
    waiting = await recipe_cost_triggers.unmatched_lines(db)
    await recipe_resolution.resolve_unmatched(db, settings)
    # Snapshots: recipes whose content changed, recipes whose waiting lines the
    # lookup settled, and recipes with none yet, once each, after the lines are
    # resolved (3D).
    changed |= await recipe_cost_triggers.settled_recipes(db, waiting)
    changed |= await recipe_cost_triggers.uncosted_recipes(db)
    await recipe_cost_triggers.after_recipes_changed(db, changed)
    for row in committed:
        await recipe_cost_triggers.mark_committed(db, row)
    for row in uncommitted:
        await recipe_cost_triggers.mark_provisional(db, row)
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
        missing_refs=[
            MissingRecipeRef(referrer_path=m.referrer_path, ref_path=m.ref_path, target=m.target)
            for m in links.missing
        ],
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
