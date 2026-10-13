"""The file-size check in tools/check_size.py, run against throwaway trees."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if not (REPO_ROOT / "tools" / "check_size.py").is_file():
    pytest.skip("needs the repository checkout, not just backend/", allow_module_level=True)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import check_size  # noqa: E402


def write_lines(root: Path, rel: str, count: int) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * count, encoding="utf-8")


def write_baseline(root: Path, baseline: dict[str, int]) -> None:
    write_lines(root, "tools/__init__.py", 0)
    (root / check_size.BASELINE_NAME).write_text(json.dumps(baseline), encoding="utf-8")


def read_baseline(root: Path) -> dict[str, int]:
    return json.loads((root / check_size.BASELINE_NAME).read_text(encoding="utf-8"))


def run(root: Path, *args: str) -> int:
    return check_size.main(["--root", str(root), *args])


def test_new_file_over_the_fail_limit_fails(tmp_path: Path, capsys) -> None:
    write_lines(tmp_path, "backend/app/services/big.py", 801)
    assert run(tmp_path) == 1
    assert "over the 800-line limit" in capsys.readouterr().err


def test_file_at_the_fail_limit_only_warns(tmp_path: Path) -> None:
    write_lines(tmp_path, "backend/app/services/big.py", 800)
    assert run(tmp_path) == 0


def test_450_lines_passes_quietly_and_520_warns_only(tmp_path: Path, capsys) -> None:
    write_lines(tmp_path, "frontend/src/pages/Middling.tsx", 450)
    assert run(tmp_path) == 0
    assert "warning:" not in capsys.readouterr().out

    write_lines(tmp_path, "frontend/src/pages/Middling.tsx", 520)
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "warning: frontend/src/pages/Middling.tsx: 520 lines" in out


def test_baselined_file_that_grows_fails(tmp_path: Path, capsys) -> None:
    write_baseline(tmp_path, {"backend/app/cli.py": 900})
    write_lines(tmp_path, "backend/app/cli.py", 901)
    assert run(tmp_path) == 1
    assert "up from 900" in capsys.readouterr().err


def test_baselined_file_that_shrinks_passes_and_update_lowers_it(tmp_path: Path) -> None:
    write_baseline(tmp_path, {"backend/app/cli.py": 900, "frontend/src/api/big.ts": 850})
    write_lines(tmp_path, "backend/app/cli.py", 880)
    write_lines(tmp_path, "frontend/src/api/big.ts", 700)
    assert run(tmp_path) == 0

    assert run(tmp_path, "--update") == 0
    # Lowered to today's count; the file back under the limit leaves for good.
    assert read_baseline(tmp_path) == {"backend/app/cli.py": 880}


def test_update_never_raises_a_count(tmp_path: Path) -> None:
    write_baseline(tmp_path, {"backend/app/cli.py": 900})
    write_lines(tmp_path, "backend/app/cli.py", 950)
    assert run(tmp_path, "--update") == 0
    assert read_baseline(tmp_path) == {"backend/app/cli.py": 900}


def test_update_drops_a_deleted_file(tmp_path: Path) -> None:
    write_baseline(tmp_path, {"backend/app/gone.py": 900})
    assert run(tmp_path, "--update") == 0
    assert read_baseline(tmp_path) == {}


@pytest.mark.parametrize(
    "rel",
    [
        "backend/tests/test_big.py",
        "frontend/src/pages/Big.test.tsx",
        "frontend/src/lib/big.test.ts",
        "e2e/tests/big.spec.ts",
    ],
)
def test_test_files_get_the_larger_limits(tmp_path: Path, rel: str) -> None:
    write_lines(tmp_path, rel, 1100)
    assert run(tmp_path) == 0
    write_lines(tmp_path, rel, 1201)
    assert run(tmp_path) == 1


def test_files_mode_checks_only_the_named_files(tmp_path: Path) -> None:
    write_lines(tmp_path, "backend/app/big.py", 900)
    write_lines(tmp_path, "backend/app/small.py", 10)
    assert run(tmp_path, "--files", "backend/app/small.py") == 0
    assert run(tmp_path, "--files", "backend/app/big.py") == 1
    # Out of scope or deleted: ignored rather than an error.
    write_lines(tmp_path, "docs/notes.py", 900)
    assert run(tmp_path, "--files", "docs/notes.py", "backend/app/missing.py") == 0


def test_out_of_scope_and_generated_files_are_skipped(tmp_path: Path) -> None:
    write_lines(tmp_path, "frontend/src/node_modules/pkg/index.ts", 5000)
    write_lines(tmp_path, "frontend/src/types.d.ts", 5000)
    write_lines(tmp_path, "backend/alembic/versions/0001_big.py", 5000)
    assert run(tmp_path) == 0


def test_report_lists_files_over_prefer_biggest_first(tmp_path: Path, capsys) -> None:
    write_lines(tmp_path, "backend/app/a.py", 450)
    write_lines(tmp_path, "backend/app/b.py", 600)
    write_lines(tmp_path, "backend/app/c.py", 100)
    assert run(tmp_path, "--report") == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[1] for line in lines] == ["backend/app/b.py", "backend/app/a.py"]


def test_the_committed_baseline_holds_today() -> None:
    baseline = check_size.load_baseline(REPO_ROOT / check_size.BASELINE_NAME)
    for rel, recorded in baseline.items():
        assert rel == Path(rel).as_posix()
        assert recorded > check_size.limits_for(rel).fail
        assert check_size.in_scope(rel)
