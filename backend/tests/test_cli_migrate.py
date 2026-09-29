"""`kerp migrate` says what it did, step by step (docker compose exec api kerp migrate).

Run as a subprocess against the test database, the way a deployment runs it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from alembic.script import ScriptDirectory

from app.cli import alembic_config
from app.core.db import dispose_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _kerp(*args: str) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


async def test_migrate_reports_each_step_and_a_summary():
    scripts = ScriptDirectory.from_config(alembic_config())
    head = scripts.get_current_head()
    previous = scripts.get_revision(head).down_revision
    two_back = scripts.get_revision(previous).down_revision
    await dispose_engine()  # nothing pooled may hold the tables the steps change
    try:
        out = _kerp("downgrade", previous)
        assert f"  reverted {head} · " in out
        assert f"Committed 1 migration. The database is at {previous}." in out

        # Two steps: one line each, in order, and the count says two.
        out = _kerp("downgrade", two_back)
        assert f"  reverted {previous} · " in out
        assert f"Committed 1 migration. The database is at {two_back}." in out
        out = _kerp("migrate")
        assert out.startswith("Migrating the database to head…\n")
        lines = [line for line in out.splitlines() if line.startswith("  ran ")]
        assert [line.split()[1] for line in lines] == [previous, head]
        assert f"Committed 2 migrations. The database is at {head}." in out

        out = _kerp("migrate")
        assert "Nothing to do: the database is already there." in out
    finally:
        # Whatever happened above, leave the schema at head for the next test.
        _kerp("migrate")
        await dispose_engine()


def test_an_unknown_revision_is_one_line_not_a_traceback():
    # Found by /devex-review: a mistyped revision printed a full traceback.
    done = subprocess.run(
        [sys.executable, "-m", "app.cli", "migrate", "nosuchrev"],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2
    assert "Can't locate revision identified by 'nosuchrev'" in done.stderr
    assert "Traceback" not in done.stderr and "│" not in done.stderr
