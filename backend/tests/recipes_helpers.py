"""A temporary recipe repository for the indexer tests (07, 3A).

Builds a git repository with dulwich under a temp dir, writes invented ``.cook``
files, commits, moves and edits them, and points the settings at it. Files are
written with their modification time pushed into the past so a scan straight
after sees them as settled; ``fresh=True`` leaves the real time for the settle
window tests. Nothing here ever shells out to ``git``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from app.core.config import get_settings

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "recipes"
FIXTURE_NAMES = sorted(p.name for p in FIXTURE_DIR.glob("index_*.cook"))
SETTLED_AGO = 60.0  # seconds; comfortably past any settle window a test sets
AUTHOR = b"Test Cook <cook@example.test>"


class TempRepo:
    """A working tree under ``root``; ``init()`` makes it a git repository."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._repo: Repo | None = None

    # --- working tree -----------------------------------------------------------

    def path(self, rel: str) -> Path:
        return self.root / rel

    def write(self, rel: str, text: str, *, fresh: bool = False) -> Path:
        target = self.path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        if not fresh:
            self.age(rel)
        return target

    def age(self, rel: str, seconds: float = SETTLED_AGO) -> None:
        """Push a file's modification time ``seconds`` into the past."""
        then = time.time() - seconds
        os.utime(self.path(rel), (then, then))

    def edit(self, rel: str, extra: str, *, fresh: bool = False) -> None:
        self.write(rel, self.path(rel).read_text(encoding="utf-8") + extra, fresh=fresh)

    def move(self, old: str, new: str) -> None:
        target = self.path(new)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(self.path(old), target)
        self.age(new)

    def remove(self, rel: str) -> None:
        self.path(rel).unlink()

    def seed_fixtures(self) -> list[str]:
        for name in FIXTURE_NAMES:
            self.write(name, (FIXTURE_DIR / name).read_text(encoding="utf-8"))
        return list(FIXTURE_NAMES)

    # --- git --------------------------------------------------------------------

    def init(self) -> None:
        self._repo = Repo.init(str(self.root))

    @property
    def repo(self) -> Repo:
        assert self._repo is not None, "call init() first"
        return self._repo

    def commit(self, message: str = "update") -> str:
        """Stage every ``.cook`` file (and every deletion) and commit; the commit id."""
        tracked = {p.decode() for p in self.repo.open_index()}
        present = {
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*.cook")
            if ".git" not in p.parts
        }
        if present:
            porcelain.add(self.repo, [str(self.root / p) for p in sorted(present)])
        gone = sorted(tracked - present)
        if gone:
            porcelain.remove(self.repo, [str(self.root / p) for p in gone])
        sha = porcelain.commit(self.repo, message=message.encode(), author=AUTHOR, committer=AUTHOR)
        return sha.decode()

    def head(self) -> str:
        return self.repo.head().decode()

    def close(self) -> None:
        if self._repo is not None:
            self._repo.close()
            self._repo = None


def snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root`` (``.git`` included) with a digest of its bytes."""
    out: dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def make_read_only(root: Path) -> None:
    for dirpath, dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        for name in dirs:
            p = Path(dirpath) / name
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    root.chmod(root.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def make_writable(root: Path) -> None:
    root.chmod(root.stat().st_mode | stat.S_IWUSR)
    for dirpath, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(dirpath) / name
            p.chmod(p.stat().st_mode | stat.S_IWUSR)


# --- pytest fixtures ------------------------------------------------------------


@pytest.fixture
def recipes_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TempRepo]:
    """An initialized, empty repository the settings point at, with a 2 s settle window."""
    root = tmp_path / "recipes"
    repo = TempRepo(root)
    repo.init()
    settings = get_settings()
    monkeypatch.setattr(settings, "recipes_path", str(root))
    monkeypatch.setattr(settings, "recipes_settle_seconds", 2)
    try:
        yield repo
    finally:
        repo.close()
        if root.exists():
            make_writable(root)
            shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def recipes_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TempRepo:
    """A plain directory with no ``.git``: a repository with no commits."""
    root = tmp_path / "recipes"
    repo = TempRepo(root)
    settings = get_settings()
    monkeypatch.setattr(settings, "recipes_path", str(root))
    monkeypatch.setattr(settings, "recipes_settle_seconds", 2)
    return repo
