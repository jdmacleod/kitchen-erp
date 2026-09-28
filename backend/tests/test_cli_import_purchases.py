"""`kerp import purchases` says what went wrong and what to do, in one line each."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent

EXPORT = {
    "format": "kitchen-erp-purchase-export/1",
    "retailer": "Invented Mart",
    "transactions": [
        {
            "ref": "T-1",
            "store_code": "0001",
            "occurred_at": "2026-03-04T18:22:00",
            "total": "3.69",
            "lines": [{"description": "OAT MILK", "qty": "1", "unit": "each", "amount": "3.69"}],
        }
    ],
}


def _kerp(path: Path, email: str = "admin@example.com") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "import",
            "purchases",
            "--from",
            str(path),
            "--as",
            email,
        ],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )


async def test_a_file_that_is_not_json_is_one_line(tmp_path: Path):
    bad = tmp_path / "export.json"
    bad.write_text("not json")
    result = _kerp(bad)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error: Export file is not valid:")


async def test_an_unmatched_store_says_how_to_match_it(tmp_path: Path):
    user = await make_user("admin")
    good = tmp_path / "export.json"
    good.write_text(json.dumps(EXPORT))
    result = _kerp(good, user.email)
    assert result.returncode == 0, result.stderr
    assert "imported 0 purchase(s)" in result.stdout
    assert "matched no location of 'Invented Mart'" in result.stderr
    assert "--location" in result.stderr
