"""Read-only access to the recipe repository through dulwich (07, 3A).

This is the only module under ``app.recipes`` that touches the filesystem. It
reads ``HEAD``, the tree it points at, and the history between two commits for
rename detection. It never writes, never refreshes the index, never takes a lock
and never spawns ``git``; a directory with no ``.git`` is a repository with no
commits, which is what a mount without history looks like to the indexer.

A ref can name an object this process cannot read yet: git writes objects and
then moves the ref, and a bind mount (Docker Desktop's in particular) can show
the new ref a moment before the new objects. That raises ``RepoChanging``, and
the scan waits for the next round instead of indexing against half a commit
(#477).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dulwich.diff_tree import CHANGE_RENAME, RenameDetector, tree_changes
from dulwich.errors import NotGitRepository
from dulwich.objects import ShaFile, Tree
from dulwich.repo import Repo


class RepoChanging(Exception):
    """A ref names an object the store does not hold yet; try again next scan."""


class RepoView:
    """A repository opened for one scan. ``repo`` is None when there is none."""

    def __init__(self, root: Path, repo: Repo | None) -> None:
        self.root = root
        self._repo = repo
        self._head: str | None = None
        self._head_tree: bytes | None = None
        if repo is not None:
            try:
                head = repo.head()
            except KeyError:
                head = None  # initialized, nothing committed yet
            if head is not None:
                self._head = head.decode("ascii")
                self._head_tree = self._object(head).tree

    @property
    def head_commit(self) -> str | None:
        """The commit ``HEAD`` points at, or None without commits or without ``.git``."""
        return self._head

    def _object(self, sha: bytes) -> ShaFile:
        """The object ``sha`` names; one the store lacks means git is mid-write."""
        assert self._repo is not None  # noqa: S101
        try:
            return self._repo.object_store[sha]
        except KeyError:
            raise RepoChanging from None

    def blob_id(self, path: str) -> str | None:
        """The blob at ``path`` (posix, relative to the root) in ``HEAD``'s tree, or None."""
        if self._repo is None or self._head_tree is None:
            return None
        # A name the tree lacks is "not in HEAD"; a tree object the store lacks is not.
        sha = self._head_tree
        for part in path.encode("utf-8").split(b"/"):
            tree = self._object(sha)
            if not isinstance(tree, Tree) or part not in tree:
                return None
            _mode, sha = tree[part]
        return sha.decode("ascii")

    def renames(self, old_commit: str, new_commit: str) -> dict[str, str]:
        """Paths git's rename detection pairs between two commits, old path to new.

        Covers moves with edits, which a pure content-hash comparison misses. An
        old commit the store no longer holds (history rewritten) yields nothing
        rather than an error: the indexer then falls through to its relink
        proposals. An object missing anywhere else is ``RepoChanging``.
        """
        if self._repo is None or old_commit == new_commit:
            return {}
        store = self._repo.object_store
        try:
            old_tree = store[old_commit.encode("ascii")].tree
        except KeyError:
            return {}
        new_tree = self._object(new_commit.encode("ascii")).tree
        out: dict[str, str] = {}
        detector = RenameDetector(store)
        try:
            for change in tree_changes(store, old_tree, new_tree, rename_detector=detector):
                if change.type == CHANGE_RENAME and change.old.path and change.new.path:
                    out[change.old.path.decode("utf-8")] = change.new.path.decode("utf-8")
        except KeyError:
            raise RepoChanging from None
        return out

    def close(self) -> None:
        if self._repo is not None:
            self._repo.close()
            self._repo = None


@contextmanager
def open_repo(root: Path) -> Iterator[RepoView]:
    """Open the repository at ``root`` for reading; a view without one when ``.git`` is absent."""
    repo: Repo | None = None
    if os.path.exists(os.path.join(root, ".git")):  # noqa: PTH110, PTH118
        try:
            repo = Repo(str(root))
        except NotGitRepository:
            repo = None
    try:
        view = RepoView(root, repo)
    except RepoChanging:
        if repo is not None:
            repo.close()
        raise
    try:
        yield view
    finally:
        view.close()
