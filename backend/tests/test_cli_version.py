"""`kerp --version` is the one command that must work on a broken deployment.

It runs in a subprocess with the database variables removed, because that is the
whole point of the test: `Settings` declares `database_url` and
`migration_database_url` without defaults, so anything that reaches
`get_settings()` raises `ValidationError` here. An in-process invocation would
still have the test harness's own environment and would prove nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _run(**extra: str) -> subprocess.CompletedProcess[str]:
    unset = {"DATABASE_URL", "MIGRATION_DATABASE_URL", "BUILD_VERSION", "BUILD_COMMIT"}
    env = {k: v for k, v in os.environ.items() if k not in unset}
    env.update(extra)
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "--version"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_works_with_no_database_configured():
    result = _run()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "kitchen-erp dev (unknown)"


def test_version_reports_the_baked_in_identity():
    result = _run(BUILD_VERSION="v0.2.0-5-gabc1234", BUILD_COMMIT="abc1234")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "kitchen-erp v0.2.0-5-gabc1234 (abc1234)"
