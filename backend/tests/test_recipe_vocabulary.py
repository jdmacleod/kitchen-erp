"""`kerp recipes vocabulary`, `lint` and `conform` (07, 1G amendments; package 9).

The catalog the commands read is built in memory here (an ``IngredientIndex``
of invented ingredients), so the CLI runs in-process under typer's CliRunner
without a second event loop over the test database; one test loads a real
context from the database to show the wiring. Recipes are invented dishes
under ``fixtures/recipes/conform``; the golden files there are what conform
must produce byte for byte.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from app import cli_recipes
from app.catalog.names import normalize_name
from app.cli import cli
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.models import Ingredient
from app.recipes.cooklang import IngredientRef, parse
from app.services import recipe_vocabulary as vocab
from app.services.catalog import IngredientIndex
from app.services.recipe_resolution import Context, load_context
from app.services.spellings import add_spelling
from tests.catalog_helpers import make_ingredient

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "recipes" / "conform"
GOLDEN = sorted(p.name for p in (FIXTURES / "before").glob("*.cook"))
NEGLIGIBLE = frozenset({"salt", "pepper", "water"})
# The invented catalog: name -> spellings. Shallot is deliberately absent, so a
# shallot name drifts to the standard-list entry, not to an ingredient.
CATALOG: dict[str, list[str]] = {
    "Garlic": [],
    "Egg": [],
    "Olive oil": ["evoo"],
    "Balsamic vinegar": [],
    "Carrot": [],
}


def fake_context(
    catalog: dict[str, list[str]] = CATALOG, *, ignored: tuple[str, ...] = ()
) -> Context:
    index = IngredientIndex()
    for name, spellings in catalog.items():
        ing = Ingredient(
            id=uuid.uuid4(), name=name, slug=name.lower().replace(" ", "-"), active=True
        )
        index.by_id[ing.id] = ing
        index.by_slug[ing.slug] = ing
        index.taken_keys.add(ing.slug)
        index.taken_names.add(name.lower())
        index.by_name.setdefault(name.lower(), []).append(ing)
        index.by_norm.setdefault(normalize_name(name), []).append(ing)
        for spelling in spellings:
            index.by_spelling.setdefault(normalize_name(spelling), []).append(ing)
    return Context(index=index, negligible=NEGLIGIBLE, ignored=frozenset(ignored))


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> Context:
    ctx = fake_context()
    monkeypatch.setattr(cli_recipes, "_context", lambda: ctx)
    return ctx


@pytest.fixture
def before(tmp_path: Path) -> Path:
    """A writable copy of the golden ``before`` files, aged so a touch would show."""
    root = tmp_path / "recipes"
    shutil.copytree(FIXTURES / "before", root)
    then = time.time() - 600
    for p in root.glob("*.cook"):
        os.utime(p, (then, then))
    return root


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli, ["recipes", *args])
    return result.exit_code, result.output


def read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as fh:
        return fh.read()


# --- the parser records where each token sits -------------------------------------------


def test_the_parser_records_token_and_name_spans() -> None:
    line = "Soften @?minced garlic{2%cloves}(for the sauce) with @salt and @olive oil {1%tbsp}."
    recipe = parse(line)
    assert not isinstance(recipe, str)
    refs = recipe.ingredients  # type: ignore[union-attr]
    spans = [(line[r.span[0] : r.span[1]], line[r.name_span[0] : r.name_span[1]]) for r in refs]
    assert spans == [
        ("@?minced garlic{2%cloves}(for the sauce)", "minced garlic"),
        ("@salt", "salt"),
        ("@olive oil {1%tbsp}", "olive oil"),
    ]
    # Spans are position, not identity: the same token anywhere compares equal.
    assert refs[1] == IngredientRef(raw_name="salt")


# --- buckets ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "bucket", "target", "note"),
    [
        ("garlic", "conformant", "Garlic", None),
        ("evoo", "conformant", "Olive oil", None),  # a spelling
        ("eggs", "inflection", "Egg", None),
        ("carrots", "inflection", "Carrot", None),
        ("minced garlic", "drift", "Garlic", "minced"),  # prep words off, exact hit
        ("peeled and diced carrots", "drift", "Carrot", "peeled and diced"),  # via inflection
        ("balsamic", "drift", "Balsamic vinegar", None),  # a standard spelling, in the catalog
        ("finely chopped shallots", "drift", "shallot", "finely chopped"),  # standard only
        ("moonberry jam", "unknown", None, None),
        ("salt", "negligible", None, None),
        ("minced salt", "unknown", None, None),  # a negligible remainder offers nothing
    ],
)
def test_classify_buckets(name: str, bucket: str, target: str | None, note: str | None) -> None:
    c = vocab.classify(fake_context(), name)
    assert (c.bucket, c.target, c.note) == (bucket, target, note)


def test_a_standard_entry_the_catalog_lacks_is_unknown_with_a_hint_not_drift() -> None:
    c = vocab.classify(fake_context(), "shallot")
    assert c.bucket == "unknown" and c.target is None
    assert c.hint is not None and "shallot" in c.hint and "catalog" in c.hint


def test_an_ignored_name_is_ignored_and_a_drift_to_it_is_unknown() -> None:
    ctx = fake_context(ignored=("moonberry",))
    assert vocab.classify(ctx, "moonberry").bucket == "ignored"
    assert vocab.classify(ctx, "chopped moonberry").bucket == "unknown"


def test_a_standard_target_held_by_an_inactive_ingredient_is_not_drift() -> None:
    ctx = fake_context()
    ing = next(iter(ctx.index.by_norm["balsamic vinegar"]))
    ing.active = False
    assert vocab.classify(ctx, "balsamic").bucket == "unknown"


async def test_classify_reads_a_context_loaded_from_the_database(
    admin_client: httpx.AsyncClient,
) -> None:
    garlic = await make_ingredient(admin_client, "Garlic")
    async with get_sessionmaker()() as db:
        await add_spelling(db, uuid.UUID(garlic["id"]), "garlic cloves", source="recipe")
        await db.commit()
        ctx = await load_context(db)
    assert vocab.classify(ctx, "garlic cloves").bucket == "conformant"
    assert vocab.classify(ctx, "minced garlic") == vocab.Classification(
        "drift", "Garlic", "ingredient", note="minced"
    )
    assert vocab.classify(ctx, "water").bucket == "negligible"


# --- the report -------------------------------------------------------------------------


def test_report_counts_files_and_names_and_writes_json(tmp_path: Path) -> None:
    root = FIXTURES / "before"
    report = vocab.build_report(fake_context(), root, vocab.read_files(root))
    assert report.files == 3 and not report.parse_errors
    by_name = {n.name_norm: n for n in report.names}
    assert by_name["minced garlic"].count == 2
    assert by_name["minced garlic"].raw_names == ["minced garlic", "Minced garlic"]
    assert by_name["minced garlic"].files == ["lantern-glazed-roots.cook"]
    assert by_name["eggs"].files == ["quiet-morning-eggs.cook", "tidepool-shallot-salad.cook"]
    assert report.counts == {
        "conformant": 2,  # olive oil, evoo
        "inflection": 2,  # eggs, carrots
        "drift": 5,  # minced/peeled garlic, balsamic, finely chopped/chopped shallots
        "unknown": 1,  # moonberry jam
        "negligible": 2,  # salt, pepper
        "ignored": 0,
    }
    # Buckets come in order, most used first within a bucket.
    assert [n.bucket for n in report.names] == sorted(
        (n.bucket for n in report.names), key=vocab._BUCKET_RANK.__getitem__
    )
    out = report.write(tmp_path / "vocabulary.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {"root", "generated_at", "files", "counts", "parse_errors", "names"}
    assert data["files"] == 3 and data["counts"]["drift"] == 5
    row = next(n for n in data["names"] if n["name_norm"] == "finely chopped shallots")
    assert row == {
        "name_norm": "finely chopped shallots",
        "bucket": "drift",
        "count": 1,
        "raw_names": ["finely chopped shallots"],
        "files": ["tidepool-shallot-salad.cook"],
        "target": "shallot",
        "target_kind": "standard",
        "note": "finely chopped",
        "hint": None,
    }


def test_report_writes_csv_by_suffix(tmp_path: Path) -> None:
    root = FIXTURES / "before"
    report = vocab.build_report(fake_context(), root, vocab.read_files(root))
    out = report.write(tmp_path / "vocabulary.csv")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "name_norm,bucket,count,target,note,hint,raw_names,files"
    assert "balsamic,drift,2,Balsamic vinegar,,,balsamic,lantern-glazed-roots.cook" in lines


def test_a_file_that_does_not_parse_is_reported_in_the_report(tmp_path: Path) -> None:
    (tmp_path / "broken.cook").write_text("Add @garlic{2 and stir.\n", encoding="utf-8")
    (tmp_path / "fine.cook").write_text("Add @garlic{2}.\n", encoding="utf-8")
    report = vocab.build_report(fake_context(), tmp_path, vocab.read_files(tmp_path))
    assert report.files == 2
    assert report.parse_errors == [
        {"path": "broken.cook", "error": "line 1, column 12: `@garlic{` is never closed"}
    ]
    assert [n.name_norm for n in report.names] == ["garlic"]


# --- kerp recipes vocabulary ------------------------------------------------------------


def test_vocabulary_prints_a_table_and_writes_the_report_under_data(
    catalog: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "data" / "vocabulary"
    monkeypatch.setattr(get_settings(), "vocabulary_reports_path", str(reports))
    code, out = run("vocabulary", "--path", str(FIXTURES / "before"))
    assert code == 0, out
    assert out.startswith("3 recipe files, 12 distinct names: 2 conformant, 2 inflection, 5 drift")
    assert "drift          2  minced garlic → Garlic, note “minced”  [1 file]" in out
    assert "finely chopped shallots → shallot (standard entry, not in the catalog yet)" in out
    [written] = list(reports.glob("vocabulary-*.json"))
    assert f"Report written to {written}" in out
    assert json.loads(written.read_text(encoding="utf-8"))["files"] == 3
    # Nothing landed beside the recipes.
    assert sorted(p.name for p in (FIXTURES / "before").iterdir()) == GOLDEN


def test_vocabulary_honours_out(catalog: Context, tmp_path: Path) -> None:
    out_file = tmp_path / "elsewhere" / "report.csv"
    code, out = run("vocabulary", "--path", str(FIXTURES / "before"), "--out", str(out_file))
    assert code == 0, out
    assert out_file.read_text(encoding="utf-8").startswith("name_norm,bucket,")


def test_commands_default_to_the_configured_mount(
    catalog: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "recipes_path", str(tmp_path / "absent"))
    code, out = run("lint")
    assert code == 2 and "pass --path" in out
    monkeypatch.setattr(get_settings(), "recipes_path", str(FIXTURES / "after"))
    code, out = run("lint")
    assert code == 0 and "moonberry jam" in out


# --- kerp recipes lint ------------------------------------------------------------------


def test_lint_reports_drift_and_unknown_with_suggestions(catalog: Context) -> None:
    code, out = run("lint", "--path", str(FIXTURES / "before"))
    assert code == 0, out
    assert "drift    minced garlic → Garlic, note “minced”  [2 lines in 1 file]" in out
    assert "drift    balsamic → Balsamic vinegar  [2 lines in 1 file]" in out
    assert "unknown  moonberry jam  [1 line in 1 file]" in out
    assert "olive oil" not in out and "salt" not in out
    assert out.rstrip().endswith("3 files: 5 drift, 1 unknown, 0 parse errors.")


def test_lint_strict_exits_1_on_drift_or_unknown_and_0_when_clean(
    catalog: Context, tmp_path: Path
) -> None:
    assert run("lint", "--path", str(FIXTURES / "before"), "--strict")[0] == 1
    # The conformed files still name moonberry jam and shallot: unknown, so strict fails.
    code, out = run("lint", "--path", str(FIXTURES / "after"), "--strict")
    assert code == 1 and "unknown  shallot (the standard list has “shallot”" in out
    clean = tmp_path / "clean"
    clean.mkdir()
    shutil.copy(FIXTURES / "before" / "quiet-morning-eggs.cook", clean)
    code, out = run("lint", "--path", str(clean), "--strict")
    assert code == 0 and out.strip() == "Every name in 1 file is a name or a spelling."


def test_lint_strict_fails_on_a_file_that_does_not_parse(catalog: Context, tmp_path: Path) -> None:
    (tmp_path / "broken.cook").write_text("Add @garlic{2 and stir.\n", encoding="utf-8")
    code, out = run("lint", "--path", str(tmp_path))
    assert code == 0 and "parse error  broken.cook: line 1, column 12" in out
    assert run("lint", "--path", str(tmp_path), "--strict")[0] == 1


# --- kerp recipes conform ---------------------------------------------------------------


def test_conform_refuses_to_run_without_a_path(catalog: Context) -> None:
    # Rich colours and wraps the usage box in CI, so strip the escapes and don't
    # rely on the option's dashes staying next to its name.
    plain = re.compile(r"\x1b\[[0-9;]*m")
    code, out = run("conform")
    assert code == 2 and "path" in plain.sub("", out)
    code, out = run("conform", "--apply")
    assert code == 2 and "path" in plain.sub("", out)


def test_conform_prints_a_diff_and_changes_nothing_without_apply(
    catalog: Context, before: Path
) -> None:
    snapshot = {p.name: read(p) for p in before.glob("*.cook")}
    code, out = run("conform", "--path", str(before))
    assert code == 0, out
    assert "--- a/lantern-glazed-roots.cook" in out and "+++ b/lantern-glazed-roots.cook" in out
    assert "-Whisk in @balsamic{2%tbsp} and a splash of @balsamic, then" in out
    assert "+Whisk in @balsamic vinegar{2%tbsp} and a splash of @balsamic vinegar{}, then" in out
    assert "quiet-morning-eggs.cook" not in out
    assert out.rstrip().endswith(
        "7 rewrites in 2 files would be made. Run with --apply to write them."
    )
    assert {p.name: read(p) for p in before.glob("*.cook")} == snapshot


def test_conform_apply_matches_the_golden_files_and_a_second_run_is_a_no_op(
    catalog: Context, before: Path
) -> None:
    untouched = before / "quiet-morning-eggs.cook"
    mtime = untouched.stat().st_mtime
    code, out = run("conform", "--path", str(before), "--apply")
    assert code == 0, out
    assert out.splitlines()[0] == "Rewrote 7 tokens in 2 files:"
    assert "  lantern-glazed-roots.cook" in out and "  tidepool-shallot-salad.cook" in out
    assert "Nothing was committed" in out
    for name in GOLDEN:
        assert (before / name).read_bytes() == (FIXTURES / "after" / name).read_bytes(), name
    assert untouched.stat().st_mtime == mtime  # a file with no drift is never opened for writing
    assert sorted(p.name for p in before.iterdir()) == GOLDEN  # nothing else was written
    # The conformed files have no drift left: the second run says so and writes nothing.
    stamps = {p.name: p.stat().st_mtime for p in before.glob("*.cook")}
    code, out = run("conform", "--path", str(before), "--apply")
    assert code == 0 and out.strip() == "No drift: nothing to rewrite."
    assert {p.name: p.stat().st_mtime for p in before.glob("*.cook")} == stamps
    report = vocab.build_report(fake_context(), before, vocab.read_files(before))
    assert report.counts["drift"] == 0


def test_conform_reports_and_skips_a_file_that_does_not_parse(
    catalog: Context, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.cook"
    broken.write_text("Add @minced garlic{2 and stir.\n", encoding="utf-8")
    fine = tmp_path / "fine.cook"
    fine.write_text("Add @minced garlic{2}.\n", encoding="utf-8")
    code, out = run("conform", "--path", str(tmp_path), "--apply")
    assert code == 0, out
    assert "parse error  broken.cook: line 1, column 19: `@minced garlic{` is never closed" in out
    assert read(broken) == "Add @minced garlic{2 and stir.\n"
    assert read(fine) == "Add @garlic{2}(minced).\n"


def test_conform_keeps_line_endings_a_bom_and_everything_outside_the_tokens(
    catalog: Context, tmp_path: Path
) -> None:
    text = (
        "﻿---\r\ntitle: Crisp moon toast\r\n---\r\n\r\n"
        "Rub @balsamic over @minced garlic{1%clove}(raw) -- @chopped garlic is a comment\r\n"
        "then add @evoo{1%tsp}.\r\n"
    )
    path = tmp_path / "toast.cook"
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    code, out = run("conform", "--path", str(tmp_path), "--apply")
    assert code == 0, out
    assert read(path) == (
        "﻿---\r\ntitle: Crisp moon toast\r\n---\r\n\r\n"
        "Rub @balsamic vinegar{} over @garlic{1%clove}(minced; raw) -- @chopped garlic is a comment"
        "\r\n"
        "then add @evoo{1%tsp}.\r\n"
    )


@pytest.mark.parametrize(
    ("line", "new_name", "note", "expected"),
    [
        ("add @balsamic now", "balsamic vinegar", None, "add @balsamic vinegar{} now"),
        (
            "add @balsamic{2%tbsp} now",
            "balsamic vinegar",
            None,
            "add @balsamic vinegar{2%tbsp} now",
        ),
        ("add @garlic now", "garlic", "minced", "add @garlic{}(minced) now"),
        ("add @?garlic{3} now", "garlic", "peeled", "add @?garlic{3}(peeled) now"),
        ("add @garlic{3}() now", "garlic", "peeled", "add @garlic{3}(peeled) now"),
        ("add @garlic {3}(raw) now", "garlic", "peeled", "add @garlic {3}(peeled; raw) now"),
        ("add @garlic now", "sun-dried tomato", None, "add @sun-dried tomato{} now"),
    ],
)
def test_rewrite_token(line: str, new_name: str, note: str | None, expected: str) -> None:
    recipe = parse(line)
    [ref] = recipe.ingredients  # type: ignore[union-attr]
    assert vocab.rewrite_token(line, ref, new_name, note) == expected
    # What was written reads back as the name and the note.
    [after] = parse(expected).ingredients  # type: ignore[union-attr]
    assert after.raw_name == new_name
    if note:
        assert after.note is not None and after.note.startswith(note)
