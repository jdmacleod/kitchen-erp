"""`kerp create-admin` is the first command a new deployment runs.

It runs in a subprocess, as an operator would run it, against the test database
the harness has already pointed the environment at. Stdin is a pipe, never a
terminal, which is exactly the `docker compose exec -T` case.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.core.db import get_sessionmaker
from app.models import AppUser

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _kerp(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "create-admin", *args],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


async def _user_count() -> int:
    async with get_sessionmaker()() as db:
        return (await db.execute(select(func.count()).select_from(AppUser))).scalar_one()


async def test_creates_an_admin_from_flags():
    result = _kerp(
        "--email", "Owner@Example.com", "--display-name", "Owner", "--password", "long-enough-pw"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("created admin owner@example.com (")


async def test_without_a_terminal_it_names_the_flags_instead_of_aborting():
    result = _kerp("--email", "owner@example.com")
    assert result.returncode == 2
    assert "Aborted" not in result.stderr
    assert "--display-name" in result.stderr and "--password" in result.stderr
    assert await _user_count() == 0


async def test_refuses_what_the_api_would_refuse():
    result = _kerp("--email", "not-an-address", "--display-name", "Owner", "--password", "a")
    assert result.returncode == 2
    assert "--email:" in result.stderr
    assert "--password:" in result.stderr
    assert "Traceback" not in result.stderr
    assert await _user_count() == 0


async def test_a_taken_email_is_one_line_not_a_traceback():
    args = (
        "--email",
        "owner@example.com",
        "--display-name",
        "Owner",
        "--password",
        "long-enough-pw",
    )
    assert _kerp(*args).returncode == 0
    result = _kerp(*args)
    assert result.returncode == 1
    assert result.stderr.strip() == "error: A user with that email already exists."
