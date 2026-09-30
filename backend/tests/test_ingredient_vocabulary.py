"""Ingredient slugs, spellings and references (03, 1G, criteria 74, 76 and 86)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import select

from app.catalog.names import STANDARD_KEY_RE
from app.core.errors import ApiError
from app.core.ids import new_id
from app.models.catalog import Ingredient, IngredientAlias
from app.services.spellings import add_spelling, vocabulary_report
from tests.catalog_helpers import make_ingredient, seed_units_via_service
from tests.conftest import run_alembic


async def _spellings(db_session, ingredient_id: str) -> dict[str, tuple[str, str]]:
    rows = await db_session.execute(
        select(IngredientAlias).where(IngredientAlias.ingredient_id == ingredient_id)
    )
    return {a.name_norm: (a.kind, a.source) for a in rows.scalars()}


async def test_new_ingredient_gets_a_local_slug_and_no_review(admin_client, db_session):
    await seed_units_via_service(db_session)
    first = await make_ingredient(admin_client, "Green onion")
    second = await make_ingredient(admin_client, "Green-onion")
    rows = {i.name: i for i in (await db_session.execute(select(Ingredient))).scalars()}
    assert rows["Green onion"].slug == "local.green-onion"
    assert rows["Green-onion"].slug == "local.green-onion-2"
    for row in rows.values():
        assert not STANDARD_KEY_RE.fullmatch(row.slug)
        assert row.reconcile_state == "not_applicable"
    assert first["id"] != second["id"]


async def test_creating_an_ingredient_adds_its_plural(admin_client, db_session):
    await seed_units_via_service(db_session)
    ing = await make_ingredient(admin_client, "Cherry tomato")
    assert await _spellings(db_session, ing["id"]) == {
        "cherry tomatoes": ("inflection", "generated")
    }
    mass = await make_ingredient(admin_client, "Rice")
    assert await _spellings(db_session, mass["id"]) == {}


async def test_a_plural_another_ingredient_has_is_skipped_not_an_error(admin_client, db_session):
    """O4: a product create whose new ingredient's plural is taken still succeeds."""
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Eggs")
    r = await admin_client.post(
        "/api/v1/products", json={"name": "Dozen large", "ingredient": {"name": "Egg"}}
    )
    assert r.status_code == 201, r.text
    egg_id = r.json()["ingredient"]["id"]
    assert await _spellings(db_session, egg_id) == {}
    report = await vocabulary_report(db_session)
    assert [(s.ingredient, s.plural, s.held_by) for s in report.skipped_plurals] == [
        ("Egg", "Eggs", "Eggs")
    ]


async def test_a_plural_held_as_a_spelling_is_skipped(admin_client, db_session):
    await seed_units_via_service(db_session)
    scallion = await make_ingredient(admin_client, "Scallion")
    await add_spelling(db_session, scallion["id"], "green onions")
    await db_session.commit()
    onion = await make_ingredient(admin_client, "Green onion")
    assert await _spellings(db_session, onion["id"]) == {}
    report = await vocabulary_report(db_session)
    assert [(s.ingredient, s.held_by) for s in report.skipped_plurals] == [
        ("Green onion", "Scallion")
    ]


async def test_a_typed_spelling_another_ingredient_has_is_refused(admin_client, db_session):
    await seed_units_via_service(db_session)
    scallion = await make_ingredient(admin_client, "Scallion")
    leek = await make_ingredient(admin_client, "Leek")
    assert await add_spelling(db_session, scallion["id"], "Green Onion")
    assert await add_spelling(db_session, scallion["id"], "green onion!") is None  # its own
    assert await add_spelling(db_session, scallion["id"], "SCALLION") is None  # its name
    with pytest.raises(ApiError) as taken:
        await add_spelling(db_session, leek["id"], "green onion")
    assert taken.value.status_code == 409 and taken.value.code == "alias_taken"
    assert taken.value.details == {"holder": "Scallion"}
    with pytest.raises(ApiError) as name_taken:
        await add_spelling(db_session, leek["id"], "scallion")
    assert name_taken.value.code == "alias_taken"


async def test_one_preferred_reference_per_system(admin_client, db_session, owner_conn):
    await seed_units_via_service(db_session)
    ing = await make_ingredient(admin_client, "Garlic")
    insert = (
        "INSERT INTO ingredient_ref (id, ingredient_id, system, external_id, is_preferred) "
        "VALUES ($1, $2, 'fdc', $3, $4)"
    )
    await owner_conn.execute(insert, new_id(), ing["id"], "1002", True)
    await owner_conn.execute(insert, new_id(), ing["id"], "1003", False)
    with pytest.raises(asyncpg.UniqueViolationError):
        await owner_conn.execute(insert, new_id(), ing["id"], "1004", True)
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(insert, new_id(), ing["id"], "not-a-number", False)
    report = await vocabulary_report(db_session)
    assert "Garlic" not in report.without_reference


async def test_report_lists_ingredients_without_a_reference(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Sorrel")
    report = await vocabulary_report(db_session)
    assert report.without_reference == ["Sorrel"]


async def test_migration_marks_existing_ingredients_unreviewed(owner_conn):
    run_alembic("downgrade", "0013")
    try:
        await owner_conn.execute(
            "INSERT INTO unit (code, dimension, to_base_factor, system, aliases) "
            "VALUES ('g', 'mass', 1, 'metric', '{}') ON CONFLICT DO NOTHING"
        )
        for name in ("Sorrel", "sorrel!", "Épinard"):
            await owner_conn.execute(
                "INSERT INTO ingredient (id, name, canonical_unit) VALUES ($1, $2, 'g')",
                new_id(),
                name,
            )
    finally:
        run_alembic("upgrade", "head")
    rows = await owner_conn.fetch(
        "SELECT name, slug, reconcile_state FROM ingredient ORDER BY created_at, id"
    )
    assert [(r["name"], r["slug"], r["reconcile_state"]) for r in rows] == [
        ("Sorrel", "local.sorrel", "unreviewed"),
        ("sorrel!", "local.sorrel-2", "unreviewed"),
        ("Épinard", "local.epinard", "unreviewed"),
    ]
    from app.core.db import dispose_engine

    await dispose_engine()


async def test_check_command_prints_the_report_and_changes_nothing(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Eggs")
    await make_ingredient(admin_client, "Egg")
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "ingredients", "check"],
        cwd=Path(__file__).resolve().parent.parent,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Plurals skipped because another ingredient has them: 1" in result.stdout
    assert "Egg: “Eggs” belongs to Eggs" in result.stdout
    assert "Ingredients without a USDA reference: 2" in result.stdout
