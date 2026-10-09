"""The repository indexer (07, 3A): scan the mount, keep ``recipe`` rows in step with it.

One scan lists ``*.cook`` files under ``RECIPES_PATH``, skips those modified
within the settle window, hashes the rest, asks dulwich for ``HEAD`` and for
renames since the last indexed commit, hands all of it to the pure planner in
``app.recipes.index`` and applies the plan. Nothing here writes under the mount.

Titles and ingredient lines come from the Cooklang parser (3B); until package 3
joins it in, the title is the file's stem and no ``recipe_ingredient`` rows are
written.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.models import Recipe
from app.recipes import index
from app.recipes.hashing import blob_sha1
from app.recipes.repo import RepoView, open_repo
from app.schemas.recipes import (
    MountState,
    RecipeOut,
    RecipesStatus,
    RecipeSummary,
    RelinkProposal,
    ScanOut,
    StatusCounts,
)

# When this process last finished a scan. The worker and the api scan in
# separate processes, so status() also looks at the rows themselves.
_last_scan: dict[str, datetime | None] = {"at": None}


@dataclass(frozen=True)
class Mount:
    root: Path
    state: MountState
    files: list[Path]


def inspect_mount(settings: Settings | None = None) -> Mount:
    """The mount's state and its ``.cook`` files, never descending into ``.git``."""
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
    if not any_entry:
        return Mount(root, "empty", [])
    if not files:
        return Mount(root, "no_cook_files", [])
    return Mount(root, "mounted", files)


def _title_for(path: str) -> str:
    # package 3: the Cooklang parser supplies the front-matter title, servings and
    # ingredient lines; until then the stem stands in so lists have a name to show.
    return Path(path).stem


def _list_files(mount: Mount, now: float, settle_seconds: float) -> list[index.DiskFile]:
    """Each file with its hash, except those still settling, which are listed unhashed.

    A file is read only once its modification time is outside the settle window,
    so a half-written file is never hashed.
    """
    out: list[index.DiskFile] = []
    for file in mount.files:
        rel = file.relative_to(mount.root).as_posix()
        try:
            mtime = file.stat().st_mtime
        except FileNotFoundError:
            continue  # gone between listing and stat: next scan sees whatever is true
        content_hash: str | None = None
        if now - mtime >= settle_seconds:
            try:
                content_hash = blob_sha1(file.read_bytes())
            except FileNotFoundError:
                continue
        out.append(index.DiskFile(rel, mtime, content_hash, title=_title_for(rel)))
    return out


def _stored(rows: list[Recipe]) -> list[index.StoredRecipe]:
    return [
        index.StoredRecipe(
            id=row.id,
            path=row.path,
            content_hash=row.content_hash,
            status=row.status,
            title=row.title,
            # package 3: the normalized ingredient names of the last good parse.
            ingredients=frozenset(),
        )
        for row in rows
    ]


def _last_indexed_head(rows: list[Recipe]) -> str | None:
    """The commit the most recent scan indexed against, from the rows it touched."""
    newest = max((r for r in rows if r.head_commit), key=lambda r: r.last_seen_at, default=None)
    return newest.head_commit if newest else None


def _mark_present(row: Recipe, repo: RepoView, now: datetime) -> None:
    """What every file on disk gets, re-indexed or not: presence, HEAD, dirtiness."""
    row.last_seen_at = now
    row.head_commit = repo.head_commit
    row.dirty = repo.blob_id(row.path) != row.content_hash
    if row.status == "missing":
        row.status = "ok"
    row.relink_candidate_id = None
    row.relink_reason = None


def _reindex(row: Recipe, content_hash: str, now: datetime) -> None:
    """The file's content changed: parse it again and replace what was derived."""
    row.content_hash = content_hash
    row.title = _title_for(row.path)
    row.status = "ok"
    row.parse_error_message = None
    # package 3: parse the file; on a parse error keep the previous
    # recipe_ingredient rows and set status/parse_error_message instead; on
    # success replace row.ingredients, servings, servings_text and front_matter.
    row.front_matter = None
    row.last_indexed_at = now


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
            proposals=0,
        )

    rows = list((await db.execute(select(Recipe))).scalars())
    files = _list_files(mount, time.time(), settings.recipes_settle_seconds)
    with open_repo(mount.root) as repo:
        renames: dict[str, str] = {}
        previous = _last_indexed_head(rows)
        if repo.head_commit and previous and previous != repo.head_commit:
            renames = repo.renames(previous, repo.head_commit)
        plan = index.plan(
            _stored(rows),
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
                case index.Update(recipe_id=rid, content_hash=h):
                    row = by_id[rid]
                    _reindex(row, h, now)
                    _mark_present(row, repo, now)
                case index.Move(recipe_id=rid, path=new_path, content_hash=h):
                    row = by_id[rid]
                    row.path = new_path
                    if row.content_hash != h:
                        _reindex(row, h, now)
                    else:
                        row.title = _title_for(new_path)
                    _mark_present(row, repo, now)
                case index.Missing(recipe_id=rid):
                    by_id[rid].status = "missing"
        # A new path never carries a row, and a moved row never takes a path that
        # has one (the planner treats a present path as the same recipe), so one
        # flush orders nothing wrongly.
        for create in plan.of(index.Create):
            row = Recipe(
                path=create.path,
                title=_title_for(create.path),
                content_hash=create.content_hash,
                head_commit=repo.head_commit,
                dirty=repo.blob_id(create.path) != create.content_hash,
                status="ok",
                last_indexed_at=now,
                last_seen_at=now,
            )
            db.add(row)
            created[create.path] = row
            if create.relink_of is not None and create.relink_reason is not None:
                relinks.append((create.relink_of, create.path, create.relink_reason))
        await db.flush()
        for origin, path, why in relinks:
            by_id[origin].relink_candidate_id = created[path].id
            by_id[origin].relink_reason = why
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
    db: AsyncSession, *, status: str | None = None, dirty: bool | None = None
) -> list[RecipeSummary]:
    stmt = select(Recipe).order_by(Recipe.path)
    if status is not None:
        stmt = stmt.where(Recipe.status == status)
    if dirty is not None:
        stmt = stmt.where(Recipe.dirty.is_(dirty))
    rows = (await db.execute(stmt)).scalars().all()
    return [RecipeSummary.model_validate(row) for row in rows]


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
    out = RecipeOut.model_validate(row, from_attributes=True)
    return out.model_copy(update={"relink": relink})


async def relink(db: AsyncSession, recipe_id: uuid.UUID, target_id: uuid.UUID) -> Recipe:
    """Confirm that a missing recipe became ``target``: the old row takes the new
    file and the new row goes, so pins and history stay with the recipe."""
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
    await db.delete(target)
    await db.flush()  # the path must be free before the old row takes it
    for name, value in carried.items():
        setattr(row, name, value)
    row.status = "parse_error" if carried["parse_error_message"] else "ok"
    row.relink_candidate_id = None
    row.relink_reason = None
    # package 3: move the target's recipe_ingredient rows across as well.
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
