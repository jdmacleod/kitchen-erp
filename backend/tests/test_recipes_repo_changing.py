"""A repository caught mid-write defers the scan instead of failing it (#477).

git writes objects and then moves the ref, and a bind mount can show the new ref
before the new objects. These tests hide an object file to stand in for that
moment: the reader says ``RepoChanging``, the scan keeps nothing and waits, and
the next scan indexes the settled repository.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.models import Recipe, RecipeCostSnapshot, RecipeIngredient
from app.recipes.repo import RepoChanging, open_repo
from app.services import recipes
from app.worker import loop_error_fields
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401

SOUP = "Simmer @red lentils{200%g} in @water{1%l}.\n"


def hide(repo: TempRepo, sha: bytes) -> Callable[[], None]:
    """Move a loose object aside, as if not written yet; the call puts it back."""
    hexsha = sha.decode("ascii")
    path = repo.root / ".git" / "objects" / hexsha[:2] / hexsha[2:]
    aside = path.with_name(path.name + ".aside")
    os.rename(path, aside)  # noqa: PTH104
    return lambda: os.rename(aside, path)  # noqa: PTH104


def subtree(repo: TempRepo, commit: str, name: bytes) -> bytes:
    tree = repo.repo[repo.repo[commit.encode()].tree]
    return tree[name][1]


async def rows() -> dict[str, Recipe]:
    async with get_sessionmaker()() as db:
        return {r.path: r for r in (await db.execute(select(Recipe))).scalars()}


async def run_scan():
    async with get_sessionmaker()() as db:
        return await recipes.scan(db)


def test_a_head_the_store_cannot_read_yet_is_changing(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("soups/lentil.cook", SOUP)
    head = recipes_repo.commit("one")
    hide(recipes_repo, head.encode())
    with pytest.raises(RepoChanging), open_repo(recipes_repo.root):
        pass


def test_a_missing_tree_is_changing_but_a_missing_name_is_not_in_head(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("soups/lentil.cook", SOUP)
    head = recipes_repo.commit("one")
    with open_repo(recipes_repo.root) as view:
        assert view.blob_id("soups/lentil.cook") is not None
        assert view.blob_id("soups/absent.cook") is None
        assert view.blob_id("soups/lentil.cook/below") is None
        hide(recipes_repo, subtree(recipes_repo, head, b"soups"))
        with pytest.raises(RepoChanging):
            view.blob_id("soups/lentil.cook")


def test_renames_wait_for_the_new_commit_but_forget_a_rewritten_old_one(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("soups/lentil.cook", SOUP)
    old = recipes_repo.commit("one")
    recipes_repo.move("soups/lentil.cook", "mains/lentil.cook")
    recipes_repo.edit("mains/lentil.cook", "Salt to taste.\n")
    new = recipes_repo.commit("move")
    with open_repo(recipes_repo.root) as view:
        assert view.renames(old, new) == {"soups/lentil.cook": "mains/lentil.cook"}
        restore = hide(recipes_repo, subtree(recipes_repo, new, b"mains"))
        with pytest.raises(RepoChanging):
            view.renames(old, new)
        restore()
        hide(recipes_repo, old.encode())
        assert view.renames(old, new) == {}


async def test_a_scan_mid_write_keeps_nothing_and_the_next_one_indexes(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("soups/lentil.cook", SOUP)
    first = recipes_repo.commit("one")
    await run_scan()
    before = (await rows())["soups/lentil.cook"]
    recipes_repo.edit("soups/lentil.cook", "Salt to taste.\n")
    recipes_repo.write("soups/pea.cook", "Simmer @split peas{200%g}.\n")
    second = recipes_repo.commit("fast-forward")
    restore = hide(recipes_repo, second.encode())

    deferred = await run_scan()
    assert deferred.mounted and deferred.changed == 0
    assert deferred.files == deferred.settling == 2
    found = await rows()
    assert sorted(found) == ["soups/lentil.cook"]
    kept = found["soups/lentil.cook"]
    assert (kept.content_hash, kept.head_commit) == (before.content_hash, first)
    assert kept.last_seen_at == before.last_seen_at

    restore()
    settled = await run_scan()
    assert settled.updated == 1 and settled.created == 1
    found = await rows()
    assert found["soups/lentil.cook"].head_commit == second
    assert not found["soups/lentil.cook"].dirty and not found["soups/pea.cook"].dirty


async def _state(path: str) -> tuple:
    """What a scan could change about one recipe: its row, its lines, its snapshots."""
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(Recipe).where(Recipe.path == path))).scalar_one()
        names = sorted(
            (
                await db.execute(
                    select(RecipeIngredient.name_norm).where(RecipeIngredient.recipe_id == row.id)
                )
            ).scalars()
        )
        snapshots = sorted(
            (h, c)
            for h, c in (
                await db.execute(
                    select(RecipeCostSnapshot.content_hash, RecipeCostSnapshot.computed_at).where(
                        RecipeCostSnapshot.recipe_id == row.id
                    )
                )
            ).tuples()
        )
        return (row.content_hash, row.dirty, row.last_indexed_at, names, snapshots)


async def test_a_scan_that_meets_a_missing_tree_after_writing_rolls_all_of_it_back(
    recipes_repo: TempRepo,  # noqa: F811
):
    """RepoChanging raised mid-plan, after a recipe was re-indexed: nothing is kept."""
    recipes_repo.write("soups/lentil.cook", SOUP)
    head = recipes_repo.commit("one")
    await run_scan()
    before = await _state("soups/lentil.cook")
    # An uncommitted edit: HEAD stays put, so the scan re-indexes the file and
    # only then asks HEAD's tree whether the file is committed.
    recipes_repo.edit("soups/lentil.cook", "Stir in @spinach{100%g}.\n")
    restore = hide(recipes_repo, subtree(recipes_repo, head, b"soups"))

    deferred = await run_scan()
    assert deferred.changed == 0 and deferred.settling == deferred.files
    assert await _state("soups/lentil.cook") == before

    restore()
    settled = await run_scan()
    assert settled.updated == 1
    content_hash, dirty, _at, names, _snapshots = await _state("soups/lentil.cook")
    assert content_hash != before[0] and dirty
    assert "spinach" in names


def test_a_worker_error_names_where_it_was_raised_and_not_its_message():
    def deepest() -> None:
        raise KeyError("a recipe's private words")

    with pytest.raises(KeyError) as caught:
        deepest()
    fields = loop_error_fields(caught.value)
    assert fields["exc_type"] == "KeyError"
    innermost = fields["where"].split(" < ")[0]
    assert innermost.startswith(f"{Path(__file__).name}:") and innermost.endswith(" deepest")
    assert "private" not in str(fields)
