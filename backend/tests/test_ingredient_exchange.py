"""Ingredient files: export, dry run, import, and the edit-wins rule (#241, spec 03 §1G).

Every ingredient, spelling, measure and USDA id here is invented.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.errors import ApiError
from app.models.catalog import Ingredient, IngredientAlias, IngredientMeasure, IngredientRef
from app.services import ingredient_exchange as ix
from app.services.spellings import add_spelling

FDC_A, FDC_B = 900001, 900002  # invented ids


def entry(key: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "name": name, "unit": "g", **extra}


def document(*entries: dict[str, Any], fmt: str = "kitchen-erp-ingredients/1") -> bytes:
    return json.dumps(
        {
            "format": fmt,
            "source": {"name": "kitchen-erp", "exported_at": "2026-10-07T12:00:00Z"},
            "ingredients": list(entries),
        }
    ).encode()


OATS = entry(
    "local.lantern-oats",
    "lantern oats",
    category="pantry",
    density={"g_per_ml": "0.41", "source": "usda", "confirmed": True},
    yield_pct="0.95",
    spellings=["lantern rolled oats"],
    fdc=[FDC_A, FDC_B],
    measures=[{"label": "1 cup", "qty": "41", "source": "usda", "confirmed": False}],
)


async def run(raw: bytes, *, dry_run: bool = False):
    async with get_sessionmaker()() as db:
        return await ix.run(db, raw, fmt="json", dry_run=dry_run, filename="test.json")


async def export() -> dict[str, Any]:
    async with get_sessionmaker()() as db:
        document_ = ix.to_document(await ix.build(db))
    document_.pop("source")
    return document_


async def ingredient(name: str) -> Ingredient | None:
    async with get_sessionmaker()() as db:
        return (await db.execute(select(Ingredient).where(Ingredient.name == name))).scalar()


async def test_an_import_creates_what_the_file_holds_and_a_second_run_changes_nothing():
    first = await run(document(OATS))
    assert (first.counts.created, first.counts.conflicts) == (1, 0)
    oats = await ingredient("lantern oats")
    assert oats is not None and oats.slug == "local.lantern-oats"
    assert (oats.category, oats.density_g_per_ml, oats.density_source) == (
        "pantry",
        Decimal("0.41"),
        "usda",
    )
    assert oats.density_confirmed and oats.yield_pct == Decimal("0.95")
    async with get_sessionmaker()() as db:
        refs = (
            await db.execute(select(IngredientRef).where(IngredientRef.ingredient_id == oats.id))
        ).scalars()
        assert {(r.external_id, r.is_preferred) for r in refs} == {
            (str(FDC_A), True),
            (str(FDC_B), False),
        }
        aliases = {
            a.name_norm: a.source
            for a in (
                await db.execute(
                    select(IngredientAlias).where(IngredientAlias.ingredient_id == oats.id)
                )
            ).scalars()
        }
        assert aliases["lantern rolled oats"] == "import"
    again = await run(document(OATS))
    assert (again.counts.unchanged, again.counts.created, again.counts.updated) == (1, 0, 0)


async def test_a_dry_run_reports_and_writes_nothing():
    report = await run(document(OATS, entry("local.juniper-bran", "juniper bran")), dry_run=True)
    assert report.dry_run and report.counts.created == 2
    assert await ingredient("lantern oats") is None


async def test_a_field_a_person_edited_is_kept_and_reported():
    await run(document(OATS))
    async with get_sessionmaker()() as db:
        oats = (
            await db.execute(select(Ingredient).where(Ingredient.name == "lantern oats"))
        ).scalar_one()
        oats.density_g_per_ml = Decimal("0.45")  # a person's measurement
        await db.commit()
    newer = {**OATS, "category": "grains", "density": {"g_per_ml": "0.5", "source": "usda"}}
    report = await run(document(newer))
    (item,) = report.items
    assert item.outcome == "conflict"
    assert [(c.field, c.current, c.file) for c in item.conflicts] == [("density", "0.45", "0.5")]
    assert [(c.field, c.value) for c in item.changes] == [("category", "grains")]
    oats = await ingredient("lantern oats")
    assert oats is not None
    assert (oats.density_g_per_ml, oats.category) == (Decimal("0.45"), "grains")


async def test_values_already_here_count_as_the_households():
    async with get_sessionmaker()() as db:
        db.add(Ingredient(name="juniper bran", canonical_unit="g", category="baking"))
        await db.commit()
    report = await run(document(entry("local.juniper-bran", "juniper bran", category="cereal")))
    (item,) = report.items
    assert item.outcome == "conflict"
    assert [(c.field, c.current, c.file) for c in item.conflicts] == [
        ("category", "baking", "cereal")
    ]
    bran = await ingredient("juniper bran")
    assert bran is not None and bran.category == "baking"


async def test_an_entry_is_matched_by_name_and_by_spelling_when_its_key_is_new():
    async with get_sessionmaker()() as db:
        bran = Ingredient(name="juniper bran", canonical_unit="g")
        db.add(bran)
        await db.flush()
        await add_spelling(db, bran.id, "juniper wheat bran")
        await db.commit()
    by_name = await run(document(entry("local.other-key", "Juniper Bran")))
    by_spelling = await run(document(entry("local.third-key", "juniper wheat bran")))
    assert by_name.counts.created == 0 and by_spelling.counts.created == 0
    async with get_sessionmaker()() as db:
        assert len((await db.execute(select(Ingredient))).scalars().all()) == 1


async def test_a_spelling_another_ingredient_holds_is_a_conflict_not_a_move():
    async with get_sessionmaker()() as db:
        db.add(Ingredient(name="lantern rolled oats", canonical_unit="g"))
        await db.commit()
    report = await run(document(OATS))
    (item,) = report.items
    assert item.outcome == "created"
    assert [c.field for c in item.conflicts] == ["spelling"]


async def test_merged_entries_are_skipped_and_merges_here_are_respected():
    await run(document(OATS))
    report = await run(
        document(entry("local.old-oats", "old oats", merged_into="local.lantern-oats"))
    )
    (item,) = report.items
    assert item.outcome == "skipped" and "merged" in (item.reason or "")
    assert await ingredient("old oats") is None


async def test_an_export_imports_into_an_empty_catalog_unchanged(owner_conn: asyncpg.Connection):
    await run(
        document(
            OATS,
            entry(
                "local.juniper-bran",
                "juniper bran",
                unit="ml",
                perishability="refrigerated",
                notes="kept cold",
            ),
        )
    )
    before = await export()
    await owner_conn.execute("TRUNCATE ingredient CASCADE")
    report = await run(
        json.dumps(
            {**before, "source": {"name": "x", "exported_at": "2026-10-07T12:00:00Z"}}
        ).encode()
    )
    assert report.counts.created == 2 and report.counts.conflicts == 0
    assert await export() == before


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        (document(OATS, fmt="kitchen-erp-ingredients/2"), "Unsupported format"),
        (document(OATS).replace(b'"0.95"', b"0.95"), "floating-point"),
        (document(OATS, OATS), "more than once"),
        (document({**OATS, "price": "1.00"}), "does not match"),
        (document({**OATS, "unit": "cup"}), "does not match"),
        (b"format: kitchen-erp-ingredients/1\nsource: &a {name: x}\n", "anchors"),
        (b"[1, 2]", "must hold one document"),
    ],
)
async def test_a_bad_file_is_refused_before_anything_is_written(raw: bytes, words: str):
    fmt = "yaml" if raw.startswith(b"format:") else "json"
    async with get_sessionmaker()() as db:
        with pytest.raises(ApiError) as caught:
            await ix.run(db, raw, fmt=fmt, dry_run=False, filename="bad")
    assert caught.value.status_code == 422 and words in caught.value.message
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(Ingredient))).scalars().all() == []


async def test_the_export_holds_no_float_and_leaves_out_empty_fields():
    await run(document(OATS, entry("local.juniper-bran", "juniper bran")))
    doc = await export()
    text = json.dumps(doc)
    assert "0.41" in text and ": 0.41" not in text  # numbers are strings
    bran = next(e for e in doc["ingredients"] if e["key"] == "local.juniper-bran")
    assert "spellings" not in bran and "density" not in bran and "fdc" not in bran


async def test_measures_are_added_and_a_different_one_here_is_kept():
    await run(document(OATS))
    async with get_sessionmaker()() as db:
        measure = (await db.execute(select(IngredientMeasure))).scalar_one()
        measure.canonical_qty = Decimal("44")
        await db.commit()
    more = {
        **OATS,
        "measures": [
            *OATS["measures"],
            {"label": "1 scoop", "qty": "30", "source": "label"},
        ],
    }
    report = await run(document(more))
    (item,) = report.items
    assert [(c.field, c.current, c.file) for c in item.conflicts] == [("measure 1 cup", "44", "41")]
    assert ("measure 1 scoop", "30") in [(c.field, c.value) for c in item.changes]
