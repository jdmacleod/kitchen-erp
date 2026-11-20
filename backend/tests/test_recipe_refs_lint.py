"""`kerp recipes lint` on recipe references (#526): missing targets, the root, cycles, yields.

In-process under typer's CliRunner with the invented catalog of the vocabulary
tests, so no database is needed. Every file is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import cli_recipes
from app.recipes.cooklang import parse
from app.recipes.refs import lint_refs
from tests.test_recipe_vocabulary import fake_context, run


@pytest.fixture(autouse=True)
def _catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = fake_context({"Sugar": [], "Water": [], "Lime juice": []})
    monkeypatch.setattr(cli_recipes, "_context", lambda: ctx)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


SYRUP = "---\nyield: 500 ml\n---\nWarm @sugar{250%g} in @water{250%ml}.\n"


def test_a_clean_reference_passes_strict(tmp_path: Path) -> None:
    _write(tmp_path, "basics/syrup.cook", SYRUP)
    _write(tmp_path, "sour.cook", "Shake @./basics/syrup{1%fl oz} with @lime juice{1%fl oz}.\n")
    code, out = run("lint", "--path", str(tmp_path), "--strict")
    assert code == 0, out


def test_a_missing_target_fails_strict(tmp_path: Path) -> None:
    _write(tmp_path, "sour.cook", "Shake @./basics/syrup{1%fl oz}.\n")
    code, out = run("lint", "--path", str(tmp_path), "--strict")
    assert code == 1
    assert "recipe   sour.cook:1  @./basics/syrup: no recipe at basics/syrup.cook" in out


def test_a_path_outside_the_root_fails_strict(tmp_path: Path) -> None:
    _write(tmp_path, "sour.cook", "Shake @../syrup{1%fl oz}.\n")
    code, out = run("lint", "--path", str(tmp_path), "--strict")
    assert code == 1 and "@../syrup leaves the recipes root" in out


def test_a_cycle_fails_strict(tmp_path: Path) -> None:
    _write(tmp_path, "a.cook", "---\nyield: 1 l\n---\nAdd @./b{100%ml}.\n")
    _write(tmp_path, "b.cook", "---\nyield: 1 l\n---\nAdd @./a{100%ml}.\n")
    code, out = run("lint", "--path", str(tmp_path), "--strict")
    assert code == 1 and "recipe references go round: a.cook → b.cook → a.cook" in out


def test_an_unparsed_yield_on_a_referenced_recipe_fails_strict(tmp_path: Path) -> None:
    _write(tmp_path, "basics/syrup.cook", "---\nyield: 2 jars\n---\nWarm @sugar{250%g}.\n")
    _write(tmp_path, "sour.cook", "Shake @./basics/syrup{1%fl oz}.\n")
    code, out = run("lint", "--path", str(tmp_path), "--strict")
    assert code == 1
    assert "basics/syrup.cook  `yield: 2 jars` isn't an amount and a unit" in out


def test_an_unparsed_yield_nobody_references_stays_quiet(tmp_path: Path) -> None:
    _write(tmp_path, "buns.cook", "---\nyield: 12 pieces\n---\nBake @sugar{20%g}.\n")
    assert lint_refs({"buns.cook": parse((tmp_path / "buns.cook").read_text())}) == []
    code, out = run("lint", "--path", str(tmp_path), "--strict")
    assert code == 0, out


def test_lint_refs_never_opens_a_file() -> None:
    """The check is on paths alone: a target outside the root is never looked up."""
    issues = lint_refs({"sour.cook": parse("Shake @../../etc/passwd{1%g}.\n")})
    assert [i.kind for i in issues] == ["outside"]
