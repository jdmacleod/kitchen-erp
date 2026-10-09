"""The repository indexer (07, 3A): the pure planner, and the scan against a temp repository.

Criteria 1–3 and 5–9. Recipes, titles and paths are invented; the repository is
built with dulwich under a temp dir and never touched by ``git``.
"""

from __future__ import annotations

import ast
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.models import Recipe
from app.recipes import index
from app.recipes.cooklang import parse
from app.recipes.hashing import blob_sha1
from app.recipes.repo import open_repo
from app.services import recipes
from tests.recipes_helpers import (
    FIXTURE_NAMES,
    TempRepo,
    make_read_only,
    make_writable,
    recipes_dir,  # noqa: F401
    recipes_repo,  # noqa: F401
    snapshot,
)

NOW = 1_000_000.0


def rid() -> uuid.UUID:
    return uuid.uuid4()


# --- hashing --------------------------------------------------------------------


def test_blob_sha1_matches_git_for_an_empty_and_a_known_blob():
    # `git hash-object` of an empty file, and of "hello\n", are well known.
    assert blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_blob_sha1_agrees_with_dulwich(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    recipes_repo.commit()
    with open_repo(recipes_repo.root) as repo:
        assert repo.blob_id("index_a.cook") == blob_sha1(
            recipes_repo.path("index_a.cook").read_bytes()
        )
        assert repo.blob_id("nope.cook") is None


# --- the pure planner -------------------------------------------------------------


def stored(path: str, h: str, status: str = "ok", title: str = "") -> index.StoredRecipe:
    return index.StoredRecipe(rid(), path, h, status, title=title or Path(path).stem)


def disk(path: str, h: str | None, age: float = 100.0, title: str = "") -> index.DiskFile:
    return index.DiskFile(path, NOW - age, h, title=title or Path(path).stem)


def plan(stored_rows, files, renames=None) -> index.Plan:
    return index.plan(stored_rows, files, now=NOW, settle_seconds=2, renames=renames)


def test_new_files_are_created_and_known_ones_seen_or_updated():
    a = stored("a.cook", "h1")
    b = stored("b.cook", "h2")
    p = plan([a, b], [disk("a.cook", "h1"), disk("b.cook", "h2x"), disk("c.cook", "h3")])
    assert p.of(index.Seen) == [index.Seen(a.id, "a.cook")]
    assert p.of(index.Update) == [index.Update(b.id, "b.cook", "h2x")]
    assert p.of(index.Create) == [index.Create("c.cook", "h3")]
    assert p.of(index.Missing) == []


def test_a_settling_file_is_left_alone_and_its_row_is_not_missing():
    a = stored("a.cook", "h1")
    p = plan([a], [disk("a.cook", None, age=0.5)])
    assert p.actions == (index.Settling("a.cook"),)
    # An unhashed file is settling whatever its mtime says.
    assert plan([], [disk("n.cook", None)]).actions == (index.Settling("n.cook"),)


def test_a_vanished_file_marks_its_row_missing_once():
    a = stored("a.cook", "h1")
    gone = stored("g.cook", "h9", status="missing")
    p = plan([a, gone], [])
    assert p.of(index.Missing) == [index.Missing(a.id, "a.cook")]


def test_step_one_a_pure_move_repoints_by_hash():
    a = stored("a.cook", "h1")
    p = plan([a], [disk("sub/a.cook", "h1")])
    assert p.actions == (index.Move(a.id, "a.cook", "sub/a.cook", "h1", "hash"),)


def test_step_two_a_git_rename_repoints_with_the_new_hash():
    a = stored("a.cook", "h1")
    p = plan([a], [disk("b.cook", "h2")], renames={"a.cook": "b.cook"})
    assert p.actions == (index.Move(a.id, "a.cook", "b.cook", "h2", "git"),)


def test_step_two_ignores_a_rename_whose_ends_are_not_gone_and_new():
    a = stored("a.cook", "h1")
    b = stored("b.cook", "h2")
    p = plan([a, b], [disk("a.cook", "h1"), disk("b.cook", "h2")], renames={"a.cook": "b.cook"})
    assert p.of(index.Move) == []


def test_step_three_proposes_a_relink_by_title_similarity():
    a = stored("lantern-lentils.cook", "h1", title="Lantern lentils")
    p = plan([a], [disk("lantern-lentils-v2.cook", "h2", title="Lantern lentils v2")])
    (create,) = p.of(index.Create)
    assert create.relink_of == a.id
    assert create.relink_reason.startswith("title similarity")
    assert p.of(index.Missing) == [index.Missing(a.id, "lantern-lentils.cook")]


def test_step_three_proposes_a_relink_by_two_thirds_ingredient_overlap():
    a = index.StoredRecipe(
        rid(), "one.cook", "h1", "ok", title="Soup", ingredients=frozenset({"a", "b", "c"})
    )
    near = index.DiskFile(
        "two.cook", NOW - 100, "h2", title="Stew", ingredients=frozenset({"a", "b"})
    )
    far = index.DiskFile("three.cook", NOW - 100, "h3", title="Pie", ingredients=frozenset({"a"}))
    p = plan([a], [near, far])
    by_path = {c.path: c for c in p.of(index.Create)}
    assert by_path["two.cook"].relink_of == a.id
    assert by_path["two.cook"].relink_reason == "2/3 ingredients in common"
    assert by_path["three.cook"].relink_of is None


def test_each_missing_recipe_gets_at_most_one_proposal_and_the_best_one():
    a = stored("pea-soup.cook", "h1", title="Pea soup")
    p = plan(
        [a],
        [
            disk("pea-soup-2.cook", "h2", title="Pea soup 2"),
            disk("pea-soups.cook", "h3", title="Pea soups"),
        ],
    )
    proposals = [c for c in p.of(index.Create) if c.relink_of is not None]
    assert len(proposals) == 1
    assert proposals[0].path == "pea-soups.cook"  # the closer title


def test_the_steps_run_in_order_hash_before_git_before_resemblance():
    a = stored("a.cook", "h1", title="Alpha")
    # The same content appears at b.cook; git also claims a.cook became c.cook.
    p = plan(
        [a],
        [disk("b.cook", "h1", title="Other"), disk("c.cook", "h2", title="Alpha")],
        renames={"a.cook": "c.cook"},
    )
    assert p.of(index.Move) == [index.Move(a.id, "a.cook", "b.cook", "h1", "hash")]
    assert p.of(index.Create) == [index.Create("c.cook", "h2")]


_path = st.from_regex(r"[a-c]{1,3}\.cook", fullmatch=True)
_hash = st.sampled_from(["h1", "h2", "h3", "h4"])


@st.composite
def scenario(draw):
    stored_paths = draw(st.lists(_path, unique=True, max_size=5))
    rows = [
        index.StoredRecipe(
            uuid.UUID(int=i + 1),
            p,
            draw(_hash),
            draw(st.sampled_from(["ok", "parse_error", "missing"])),
            title=p,
        )
        for i, p in enumerate(stored_paths)
    ]
    disk_paths = draw(st.lists(_path, unique=True, max_size=5))
    files = [
        index.DiskFile(
            p,
            NOW - draw(st.floats(min_value=0, max_value=10)),
            draw(st.one_of(st.none(), _hash)),
            title=p,
        )
        for p in disk_paths
    ]
    renames = draw(st.dictionaries(_path, _path, max_size=3))
    return rows, files, renames


@given(scenario())
@settings(max_examples=300)
def test_plan_is_deterministic_and_places_every_path_exactly_once(data):
    rows, files, renames = data
    first = plan(rows, files, renames)
    assert first == plan(list(reversed(rows)), list(reversed(files)), dict(renames))
    placed = [
        a.path
        for a in first.actions
        if isinstance(a, index.Settling | index.Seen | index.Update | index.Move | index.Create)
    ]
    assert sorted(placed) == sorted(f.path for f in files)
    assert len(placed) == len(set(placed))
    # Each stored row is acted on at most once, and Missing never names a missing row.
    touched = [a.recipe_id for a in first.actions if hasattr(a, "recipe_id")]
    assert len(touched) == len(set(touched))
    missing_ids = {r.id for r in rows if r.status == "missing"}
    assert not any(a.recipe_id in missing_ids for a in first.of(index.Missing))
    # A relink proposal never names a row that is still on disk or being moved.
    moved = {a.recipe_id for a in first.of(index.Move)}
    seen = {a.recipe_id for a in first.actions if isinstance(a, index.Seen | index.Update)}
    for c in first.of(index.Create):
        if c.relink_of is not None:
            assert c.relink_of not in moved | seen


# --- purity (criterion 8, the subprocess half) -------------------------------------

RECIPES_DIR = Path(recipes.__file__).resolve().parent.parent / "recipes"
PURE_FORBIDDEN = (
    "os",
    "pathlib",
    "io",
    "subprocess",
    "dulwich",
    "sqlalchemy",
    "app.core",
    "app.models",
    "app.services",
    "app.api",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_nothing_under_app_recipes_spawns_a_process():
    files = list(RECIPES_DIR.rglob("*.py"))
    assert files
    for file in files:
        for name in imported_modules(file):
            assert not name.startswith(("subprocess", "os.system", "asyncio.subprocess")), (
                f"{file.name} imports {name}"
            )
        assert "subprocess" not in file.read_text(), file.name


def test_hashing_and_the_planner_perform_no_io():
    for name in ("hashing.py", "index.py"):
        for module in imported_modules(RECIPES_DIR / name):
            assert not module.startswith(PURE_FORBIDDEN), f"{name} imports {module}"


# --- the scan against a temporary repository ---------------------------------------


async def rows() -> dict[str, Recipe]:
    async with get_sessionmaker()() as db:
        found = (await db.execute(select(Recipe))).scalars().all()
        return {r.path: r for r in found}


async def run_scan():
    async with get_sessionmaker()() as db:
        return await recipes.scan(db)


async def test_criterion_1_five_recipes_index_once(recipes_repo: TempRepo):  # noqa: F811
    names = recipes_repo.seed_fixtures()
    recipes_repo.commit("five recipes")
    first = await run_scan()
    assert first.mounted and first.created == 5 and first.files == 5
    found = await rows()
    assert sorted(found) == names
    for name in names:
        row = found[name]
        assert row.title == parse(recipes_repo.path(name).read_text()).title
        assert row.content_hash == blob_sha1(recipes_repo.path(name).read_bytes())
        assert row.status == "ok" and not row.dirty
        assert row.head_commit == recipes_repo.head()
    indexed = {name: r.last_indexed_at for name, r in found.items()}
    second = await run_scan()
    assert second.changed == 0 and second.created == 0 and second.updated == 0
    assert {name: r.last_indexed_at for name, r in (await rows()).items()} == indexed


async def test_criterion_2_a_fresh_file_waits_for_the_settle_window(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("index_fresh.cook", "Stir @oats{50%g}.\n", fresh=True)
    first = await run_scan()
    assert first.settling == 1 and first.created == 0
    assert await rows() == {}
    recipes_repo.age("index_fresh.cook")
    second = await run_scan()
    assert second.created == 1 and second.settling == 0
    assert "index_fresh.cook" in await rows()


async def test_criterion_2_an_edit_in_the_window_keeps_the_old_row_untouched(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    await run_scan()
    before = (await rows())["index_a.cook"].content_hash
    recipes_repo.edit("index_a.cook", "Add @milk{1%cup}.\n", fresh=True)
    result = await run_scan()
    assert result.settling == 1 and result.updated == 0 and result.missing == 0
    row = (await rows())["index_a.cook"]
    assert row.content_hash == before and row.status == "ok"


async def test_criterion_3_an_edit_is_dirty_until_committed(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    recipes_repo.commit("add")
    await run_scan()
    assert not (await rows())["index_a.cook"].dirty
    recipes_repo.edit("index_a.cook", "Add @milk{1%cup}.\n")
    assert (await run_scan()).updated == 1
    row = (await rows())["index_a.cook"]
    assert row.dirty
    indexed_at, edited_hash = row.last_indexed_at, row.content_hash
    recipes_repo.commit("edit")
    result = await run_scan()
    assert result.updated == 0  # the content did not change: no re-parse
    row = (await rows())["index_a.cook"]
    assert not row.dirty
    assert row.content_hash == edited_hash and row.last_indexed_at == indexed_at
    assert row.head_commit == recipes_repo.head()


async def test_a_file_not_in_head_is_dirty_and_untracked(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    recipes_repo.commit("add")
    recipes_repo.write("index_b.cook", "Whisk @egg{2}.\n")
    await run_scan()
    found = await rows()
    assert not found["index_a.cook"].dirty
    assert found["index_b.cook"].dirty


async def test_a_vanished_file_is_missing_and_its_row_kept(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    recipes_repo.write("index_b.cook", "Whisk @egg{2}.\n")
    await run_scan()
    old = (await rows())["index_a.cook"]
    recipes_repo.remove("index_a.cook")
    assert (await run_scan()).missing == 1
    row = (await rows())["index_a.cook"]
    assert row.id == old.id and row.status == "missing"
    assert (await run_scan()).missing == 0  # marked once
    assert (await rows())["index_b.cook"].status == "ok"
    # Back again at the same path: ok, no re-index needed.
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    result = await run_scan()
    assert result.updated == 0 and result.created == 0
    assert (await rows())["index_a.cook"].status == "ok"


async def test_removing_the_last_recipe_in_a_repository_marks_it_missing(
    recipes_repo: TempRepo,  # noqa: F811
):
    """A ``.git`` directory counts as a repository (package 2, 2026-10-09)."""
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    await run_scan()
    recipes_repo.remove("index_a.cook")
    result = await run_scan()
    assert result.mounted and result.files == 0 and result.missing == 1
    async with get_sessionmaker()() as db:
        status = await recipes.status(db)
    assert status.mount == "mounted" and status.counts.missing == 1


async def test_an_emptied_directory_without_git_touches_no_row(recipes_dir: TempRepo):  # noqa: F811
    """No ``.git`` and no ``.cook`` files is no repository: rows are left as they are."""
    recipes_dir.write("index_a.cook", "Stir @oats{50%g}.\n")
    await run_scan()
    recipes_dir.remove("index_a.cook")
    result = await run_scan()
    assert not result.mounted and result.missing == 0
    async with get_sessionmaker()() as db:
        status = await recipes.status(db)
    assert status.mount == "empty" and status.counts.ok == 1
    recipes_dir.write("notes.txt", "not a recipe\n")
    async with get_sessionmaker()() as db:
        assert (await recipes.status(db)).mount == "no_cook_files"
    assert not (await run_scan()).mounted
    assert (await rows())["index_a.cook"].status == "ok"


async def test_criterion_5_a_pure_move_keeps_the_id(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    recipes_repo.commit("add")
    await run_scan()
    old = (await rows())["index_a.cook"]
    recipes_repo.move("index_a.cook", "soups/index_a.cook")
    result = await run_scan()
    assert result.moved == 1 and result.created == 0 and result.missing == 0
    found = await rows()
    assert list(found) == ["soups/index_a.cook"]
    row = found["soups/index_a.cook"]
    assert row.id == old.id and row.content_hash == old.content_hash
    assert row.dirty  # untracked at its new path until committed
    recipes_repo.commit("move")
    await run_scan()
    assert not (await rows())["soups/index_a.cook"].dirty


async def test_a_present_path_is_the_same_recipe_even_when_its_content_moved_elsewhere(
    recipes_repo: TempRepo,  # noqa: F811
):
    """Path first (07, 3A): a path still on disk is updated, never treated as moved."""
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    await run_scan()
    old = (await rows())["index_a.cook"]
    recipes_repo.move("index_a.cook", "soups/index_a.cook")
    recipes_repo.write("index_a.cook", "Whisk @egg{2}.\n")
    result = await run_scan()
    assert result.updated == 1 and result.created == 1 and result.moved == 0
    after = await rows()
    assert after["index_a.cook"].id == old.id
    assert after["soups/index_a.cook"].id != old.id


async def test_criterion_6_a_committed_move_with_an_edit_is_repointed_by_git(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.seed_fixtures()
    recipes_repo.commit("five")
    await run_scan()
    old = (await rows())["index_lantern_lentils.cook"]
    recipes_repo.move("index_lantern_lentils.cook", "mains/index_glow_lentils.cook")
    recipes_repo.edit("mains/index_glow_lentils.cook", "\nServe with @rice{200%g}.\n")
    recipes_repo.commit("move and edit")
    result = await run_scan()
    assert result.moved == 1 and result.created == 0 and result.missing == 0
    found = await rows()
    row = found["mains/index_glow_lentils.cook"]
    assert row.id == old.id
    assert row.content_hash != old.content_hash and not row.dirty
    assert row.last_indexed_at > old.last_indexed_at
    assert "index_lantern_lentils.cook" not in found


async def test_criterion_7_an_uncommitted_move_with_an_edit_proposes_a_relink(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.seed_fixtures()
    recipes_repo.commit("five")
    await run_scan()
    old = (await rows())["index_lantern_lentils.cook"]
    recipes_repo.move("index_lantern_lentils.cook", "index_lantern_lentils_v2.cook")
    recipes_repo.edit("index_lantern_lentils_v2.cook", "\nServe with @rice{200%g}.\n")
    result = await run_scan()
    assert result.created == 1 and result.missing == 1 and result.proposals == 1
    found = await rows()
    missing = found["index_lantern_lentils.cook"]
    new = found["index_lantern_lentils_v2.cook"]
    assert missing.id == old.id and missing.status == "missing"
    assert missing.relink_candidate_id == new.id
    assert missing.relink_reason.startswith("title similarity")
    # Confirming repoints the row and drops the new one.
    async with get_sessionmaker()() as db:
        await recipes.relink(db, missing.id, new.id)
    found = await rows()
    assert set(found) == (set(FIXTURE_NAMES) - {"index_lantern_lentils.cook"}) | {
        "index_lantern_lentils_v2.cook"
    }
    row = found["index_lantern_lentils_v2.cook"]
    assert row.id == old.id and row.status == "ok" and row.dirty
    assert row.content_hash == new.content_hash and row.relink_candidate_id is None
    # A later scan sees the relinked row as settled, without creating anything.
    assert (await run_scan()).changed == 0


async def test_criterion_7_refusing_keeps_the_missing_recipe_until_removed(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("index_pea_soup.cook", "Simmer @peas{300%g}.\n")
    recipes_repo.commit("add")
    await run_scan()
    old = (await rows())["index_pea_soup.cook"]
    recipes_repo.move("index_pea_soup.cook", "index_pea_soup_two.cook")
    recipes_repo.edit("index_pea_soup_two.cook", "Add @mint{some}.\n")
    await run_scan()
    found = await rows()
    assert found["index_pea_soup.cook"].relink_candidate_id == found["index_pea_soup_two.cook"].id
    # Refusing is doing nothing: the proposal stays, the history stays, scans are quiet.
    assert (await run_scan()).changed == 0
    found = await rows()
    assert found["index_pea_soup.cook"].id == old.id
    assert found["index_pea_soup.cook"].relink_candidate_id is not None
    async with get_sessionmaker()() as db:
        await recipes.delete_recipe(db, old.id)
    assert list(await rows()) == ["index_pea_soup_two.cook"]


async def test_criterion_8_a_scan_changes_nothing_under_the_mount(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.seed_fixtures()
    recipes_repo.commit("five")
    recipes_repo.edit("index_comet_crumble.cook", "Serve warm.\n")  # a dirty file as well
    recipes_repo.move("index_harbor_flatbread.cook", "breads/index_harbor_flatbread.cook")
    recipes_repo.close()  # the harness's own handle; the scan opens its own
    before = snapshot(recipes_repo.root)
    assert any(p.startswith(".git/") for p in before)
    await run_scan()
    await run_scan()
    assert snapshot(recipes_repo.root) == before
    assert not (recipes_repo.root / ".git" / "index.lock").exists()
    # And on a read-only tree, which is how Compose mounts it.
    make_read_only(recipes_repo.root)
    try:
        result = await run_scan()
        assert result.mounted and result.files == 5
        assert snapshot(recipes_repo.root) == before
        assert not (recipes_repo.root / ".git" / "index.lock").exists()
    finally:
        make_writable(recipes_repo.root)


async def test_criterion_9_no_repository_is_reported_and_harmless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = get_settings()
    missing = tmp_path / "nowhere"
    monkeypatch.setattr(settings, "recipes_path", str(missing))
    async with get_sessionmaker()() as db:
        assert (await recipes.status(db)).mount == "missing"
        assert not (await recipes.scan(db)).mounted
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(settings, "recipes_path", str(empty))
    async with get_sessionmaker()() as db:
        assert (await recipes.status(db)).mount == "empty"
        assert not (await recipes.scan(db)).mounted
    (empty / "README.md").write_text("no recipes here\n")
    async with get_sessionmaker()() as db:
        assert (await recipes.status(db)).mount == "no_cook_files"
        assert not (await recipes.scan(db)).mounted
    assert await rows() == {}


async def test_a_directory_without_git_is_a_repository_with_no_commits(recipes_dir: TempRepo):  # noqa: F811
    recipes_dir.seed_fixtures()
    result = await run_scan()
    assert result.mounted and result.created == 5
    for row in (await rows()).values():
        assert row.dirty and row.head_commit is None
    async with get_sessionmaker()() as db:
        status = await recipes.status(db)
    assert status.mount == "mounted" and status.head_commit is None
    assert status.counts.ok == 5 and status.total == 5


async def test_an_initialized_repository_without_commits_is_the_same(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    await run_scan()
    row = (await rows())["index_a.cook"]
    assert row.dirty and row.head_commit is None


async def test_status_counts_and_reports_the_last_scan(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    recipes_repo.write("index_b.cook", "Whisk @egg{2}.\n")
    recipes_repo.commit("two")
    started = datetime.now(UTC) - timedelta(seconds=1)
    await run_scan()
    recipes_repo.remove("index_b.cook")
    await run_scan()
    async with get_sessionmaker()() as db:
        status = await recipes.status(db)
    assert status.mounted and status.head_commit == recipes_repo.head()
    assert status.counts.ok == 1 and status.counts.missing == 1 and status.total == 2
    assert status.last_scan_at is not None and status.last_scan_at >= started


def test_the_mount_listing_skips_dot_directories(recipes_dir: TempRepo):  # noqa: F811
    recipes_dir.write("index_a.cook", "Stir @oats{50%g}.\n")
    recipes_dir.write(".hidden/index_b.cook", "Whisk @egg{2}.\n")
    mount = recipes.inspect_mount(get_settings())
    assert [p.name for p in mount.files] == ["index_a.cook"]
    assert os.path.isdir(recipes_dir.root / ".hidden")
