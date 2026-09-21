"""The conversion library performs no I/O: it imports nothing that could."""

import ast
from pathlib import Path

UNITS_DIR = Path(__file__).resolve().parent.parent / "app" / "units"
FORBIDDEN_PREFIXES = (
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "socket",
    "os",
    "pathlib",
    "io",
    "subprocess",
    "app.core",
    "app.models",
    "app.services",
    "app.api",
    "fastapi",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_units_package_imports_no_io():
    files = list(UNITS_DIR.glob("*.py"))
    assert files
    for file in files:
        for name in imported_modules(file):
            assert not name.startswith(FORBIDDEN_PREFIXES), f"{file.name} imports {name}"
            assert (
                name.startswith("app.units")
                or "." not in name
                or name.startswith(("collections", "typing"))
            ), f"{file.name} imports {name}"
