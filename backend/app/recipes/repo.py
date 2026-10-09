"""Read-only access to the recipe repository through dulwich (07, 3A).

This is the only module under ``app.recipes`` that touches the filesystem. It
reads ``HEAD``, the tree it points at, and the history between two commits for
rename detection. It never writes, never refreshes the index, never takes a lock
and never spawns ``git``; a directory with no ``.git`` is a repository with no
commits, which is what a mount without history looks like to the indexer.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dulwich.diff_tree import CHANGE_RENAME, RenameDetector, tree_changes
from dulwich.errors import NotGitRepository
from dulwich.object_store import tree_lookup_path
from dulwich.repo import Repo


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
                self._head_tree = repo[head].tree

    @property
    def head_commit(self) -> str | None:
        """The commit ``HEAD`` points at, or None without commits or without ``.git``."""
        return self._head

    def blob_id(self, path: str) -> str | None:
        """The blob at ``path`` (posix, relative to the root) in ``HEAD``'s tree, or None."""
        if self._repo is None or self._head_tree is None:
            return None
        try:
            _mode, sha = tree_lookup_path(
                self._repo.object_store.__getitem__, self._head_tree, path.encode("utf-8")
            )
        except (KeyError, NotADirectoryError):
            return None
        return sha.decode("ascii")

    def renames(self, old_commit: str, new_commit: str) -> dict[str, str]:
        """Paths git's rename detection pairs between two commits, old path to new.

        Covers moves with edits, which a pure content-hash comparison misses. A
        commit the store no longer holds (history rewritten) yields nothing rather
        than an error: the indexer then falls through to its relink proposals.
        """
        if self._repo is None or old_commit == new_commit:
            return {}
        store = self._repo.object_store
        try:
            old_tree = self._repo[old_commit.encode("ascii")].tree
            new_tree = self._repo[new_commit.encode("ascii")].tree
        except KeyError:
            return {}
        out: dict[str, str] = {}
        detector = RenameDetector(store)
        for change in tree_changes(store, old_tree, new_tree, rename_detector=detector):
            if change.type == CHANGE_RENAME and change.old.path and change.new.path:
                out[change.old.path.decode("utf-8")] = change.new.path.decode("utf-8")
        return out

    def close(self) -> None:
        if self._repo is not None:
            self._repo.close()
            self._repo = None


@contextmanager
def open_repo(root: Path) -> Iterator[RepoView]:
    """Open the repository at ``root`` for reading; a view without one when ``.git`` is absent."""
    repo: Repo | None = None
    if os.path.exists(os.path.join(root, ".git")):
        try:
            repo = Repo(str(root))
        except NotGitRepository:
            repo = None
    view = RepoView(root, repo)
    try:
        yield view
    finally:
        view.close()
