"""`kerp recipes rescan` (package 10): one scan of the configured mount, from the host.

Run as a subprocess, like `kerp --version`: the command opens its own engine
under ``asyncio.run``, which cannot share the test harness's event loop. The
database variables already point at the throwaway test database (conftest
rewrites them), and ``RECIPES_PATH`` points at a temporary directory of invented
files. ``clean_tables`` empties what the scan wrote.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _run(recipes_path: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "RECIPES_PATH": str(recipes_path), "RECIPES_SETTLE_SECONDS": "0"}
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "recipes", "rescan"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_rescan_indexes_the_mount_and_reports_counts(tmp_path: Path) -> None:
    root = tmp_path / "recipes"
    (root / "mains").mkdir(parents=True)
    (root / "mains" / "soup.cook").write_text(
        "---\ntitle: Test soup\nservings: 2\n---\n\nSimmer @carrots{2} in @water{1%l}.\n",
        encoding="utf-8",
    )
    (root / "broken.cook").write_text("Stir @egg{1 into the pan.\n", encoding="utf-8")

    first = _run(root)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == (
        "Scanned 2 files: 2 new, 0 changed, 0 moved, 0 missing, 1 parse error, 0 still settling."
    )

    # A second scan of the same files changes nothing.
    second = _run(root)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == (
        "Scanned 2 files: 0 new, 0 changed, 0 moved, 0 missing, 0 parse errors, 0 still settling."
    )


def test_rescan_without_a_mount_says_so(tmp_path: Path) -> None:
    result = _run(tmp_path / "nowhere")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "No recipes directory is mounted: nothing to scan."
