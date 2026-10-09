"""The recipe libraries perform no I/O: `app/recipes/` imports nothing that could (07, 3B, 13).

Modelled on test_units_purity.py. Every module under `app/recipes/`, at any
depth, is checked: no database, no network, no filesystem, no subprocess, and
nothing from the service or API layers. Later sub-phases that need I/O (the
3A indexer reads a git tree) must keep that code outside this package or
widen this test deliberately, in the same commit.
"""

from __future__ import annotations

import ast
from pathlib import Path

RECIPES_DIR = Path(__file__).resolve().parent.parent / "app" / "recipes"
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
    "shutil",
    "tempfile",
    "subprocess",
    "asyncio",
    "dulwich",
    "app.core",
    "app.models",
    "app.services",
    "app.api",
    "app.ingest",
    "fastapi",
)
ALLOWED_DOTTED = ("app.recipes", "app.units", "collections", "typing")
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__"}


def imported_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def called_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def test_recipes_package_imports_no_io() -> None:
    files = sorted(RECIPES_DIR.rglob("*.py"))
    assert files
    assert any(file.parent.name == "cooklang" for file in files)
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for name in imported_modules(tree):
            assert not name.startswith(FORBIDDEN_PREFIXES), f"{file.name} imports {name}"
            assert name.startswith(ALLOWED_DOTTED) or "." not in name, f"{file.name} imports {name}"
        assert not called_names(tree) & FORBIDDEN_CALLS, f"{file.name} calls I/O or code execution"


def test_yaml_is_only_ever_loaded_safely() -> None:
    for file in sorted(RECIPES_DIR.rglob("*.py")):
        source = file.read_text(encoding="utf-8")
        if "yaml" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in {"load", "load_all", "unsafe_load", "full_load"}:
                continue
            loaders = [kw.value for kw in node.keywords if kw.arg == "Loader"]
            assert node.func.attr == "load" and loaders, (
                f"{file.name}: yaml.{node.func.attr} without Loader="
            )
            assert isinstance(loaders[0], ast.Name) and loaders[0].id == "_VerbatimLoader", (
                file.name
            )
