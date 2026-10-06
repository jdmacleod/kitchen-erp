"""Migration 0030 re-keys receipt wording with normalizer version 2, and back (#125).

The plan is pure and checked on its own first. Then one invented household is
seeded at revision 0029 with version 1 keys, upgraded, checked, downgraded and
checked again. Every vendor, product and wording here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from hypothesis import given
from hypothesis import strategies as st

from app.services.normalize import NORMALIZE_VERSION, normalize_receipt_text
from tests.conftest import BACKEND_ROOT, run_alembic
from tests.normalize_v1 import normalize_v1
from tests.pricebook_helpers import make_location, make_product
from tests.resolution_helpers import make_receipt_purchase

_path = BACKEND_ROOT / Path("alembic/versions/0030_normalize_v2.py")
_spec = importlib.util.spec_from_file_location("migration_0030", _path)
assert _spec is not None and _spec.loader is not None
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)
CLI = "from app.cli import cli; cli()"
V = uuid.uuid4()


# --- the frozen normalizer -----------------------------------------------------------


@given(st.text(st.sampled_from(list("AB TX019.,@$/-*&%'LBOZ")), max_size=40))
def test_frozen_copy_matches_the_module_at_version_2(raw: str):
    # When the module moves on, this copy stays: the migration must not change.
    if NORMALIZE_VERSION == "2":
        assert migration.normalize_v2(raw) == normalize_receipt_text(raw)


# --- the plan --------------------------------------------------------------------------


def line(raw: str, *, resolution="unmatched", auto=True, product=None, removed=False, vendor=V):
    return {
        "id": uuid.uuid4(),
        "vendor_id": vendor,
        "raw_text": raw,
        "raw_text_norm": normalize_v1(raw),
        "resolution": resolution,
        "auto": auto,
        "product_id": product,
        "removed": removed,
    }


def alias(key: str, product=None, *, count=1, seen=0, created=0, vendor=V):
    return {
        "id": uuid.uuid4(),
        "vendor_id": vendor,
        "raw_text_norm": key,
        "disposition": "product" if product else "ignore",
        "product_id": product,
        "confirmed_count": count,
        "last_seen_at": T0 + timedelta(days=seen),
        "created_at": T0 + timedelta(days=created),
    }


def test_a_comma_price_line_takes_the_point_price_key():
    a, b, c = line("KELP CRISPS 7,25"), line("KELP CRISPS 7.25"), line("OAT ROUNDS")
    p = migration.plan([a, b, c], [], [])
    assert p.line_keys == {a["id"]: "KELP CRISPS"}


def test_an_alias_follows_its_lines_to_the_new_key():
    kelp = uuid.uuid4()
    a = alias("KELP CRISPS 7 25", kelp)
    p = migration.plan([line("KELP CRISPS 7,25")], [a], [])
    assert p.alias_updates == {a["id"]: {"raw_text_norm": "KELP CRISPS"}}
    assert p.alias_deletes == [] and p.lines_returned == []


def test_an_alias_with_no_lines_keeps_its_key():
    p = migration.plan([], [alias("PLUM BAR 2 10", uuid.uuid4())], [])
    assert p.alias_updates == {} and p.alias_deletes == []


def test_the_same_decision_merges_into_the_oldest_alias():
    kelp = uuid.uuid4()
    old = alias("KELP CRISPS", kelp, count=3, seen=1, created=0)
    new = alias("KELP CRISPS 6 10", kelp, count=2, seen=5, created=2)
    p = migration.plan([line("KELP CRISPS 6,10")], [old, new], [])
    assert p.alias_updates == {
        old["id"]: {"confirmed_count": 5, "last_seen_at": new["last_seen_at"]}
    }
    assert p.alias_deletes == [new["id"]]
    assert p.aliases_merged == 1 and p.lines_returned == []


def test_a_different_decision_is_outvoted_and_its_own_lines_return():
    kelp, large = uuid.uuid4(), uuid.uuid4()
    winner = alias("KELP CRISPS", kelp, count=3)
    loser = alias("KELP CRISPS 7 25", large, count=1)
    by_alias = line("KELP CRISPS 7,25", resolution="alias", product=large)
    by_person = line("KELP CRISPS 7,25", resolution="manual", auto=False, product=large)
    removed = line("KELP CRISPS 7,25", resolution="alias", product=large, removed=True)
    agreeing = line("KELP CRISPS 7.25", resolution="alias", product=kelp)
    p = migration.plan([by_alias, by_person, removed, agreeing], [winner, loser], [])
    assert p.alias_deletes == [loser["id"]]
    assert p.aliases_outvoted == 1
    assert p.lines_returned == [by_alias["id"]]


def test_a_tie_goes_to_the_most_recently_seen():
    kelp, large = uuid.uuid4(), uuid.uuid4()
    older = alias("KELP CRISPS", kelp, count=2, seen=1)
    recent = alias("KELP CRISPS 7 25", large, count=2, seen=4)
    p = migration.plan([line("KELP CRISPS 7,25")], [older, recent], [])
    assert p.alias_deletes == [older["id"]]
    assert p.alias_updates == {recent["id"]: {"raw_text_norm": "KELP CRISPS"}}


def test_ignore_against_a_product_is_a_different_decision():
    kelp = uuid.uuid4()
    ignore = alias("KELP CRISPS 7 25", None, count=4)
    product = alias("KELP CRISPS", kelp, count=1)
    ignored = line("KELP CRISPS 7,25", resolution="ignored")
    p = migration.plan([ignored, line("KELP CRISPS 7.25")], [ignore, product], [])
    assert p.alias_deletes == [product["id"]]
    assert p.lines_returned == []  # the ignore decision won; its own line stays ignored


def test_aliases_at_other_vendors_never_meet():
    other = uuid.uuid4()
    a = alias("KELP CRISPS", uuid.uuid4())
    b = alias("KELP CRISPS 7 25", uuid.uuid4(), vendor=other)
    p = migration.plan([line("KELP CRISPS 7,25", vendor=other)], [a, b], [])
    assert p.alias_deletes == []
    assert p.alias_updates == {b["id"]: {"raw_text_norm": "KELP CRISPS"}}


def test_a_suggestion_made_from_a_price_fragment_is_dropped():
    stale = {"id": uuid.uuid4(), "vendor_id": V, "raw_text_norm": "FIG SPREAD 3 40"}
    kept = {"id": uuid.uuid4(), "vendor_id": V, "raw_text_norm": "FIG SPREAD"}
    p = migration.plan([line("FIG SPREAD 3,40"), line("FIG SPREAD 3.40")], [], [stale, kept])
    assert p.suggestion_deletes == [stale["id"]]


# --- the round trip ----------------------------------------------------------------------


async def _engine_reset() -> None:
    from app.core.db import dispose_engine

    await dispose_engine()


async def _aliases(conn: asyncpg.Connection) -> dict[uuid.UUID, dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT id, raw_text_norm, product_id, confirmed_count, last_seen_at, created_at "
        "FROM receipt_alias"
    )
    return {r["id"]: dict(r) for r in rows}


async def _lines(conn: asyncpg.Connection) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT raw_text, raw_text_norm, resolution, product_id, resolved_by, suggestions, flags "
        "FROM purchase_line"
    )
    return {r["raw_text"]: dict(r) for r in rows}


async def test_keys_move_to_version_2_and_come_back(admin_client, admin, owner_conn):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    vendor = await owner_conn.fetchval(
        "SELECT vendor_id FROM vendor_location WHERE id = $1", uuid.UUID(loc["id"])
    )
    kelp = uuid.UUID((await make_product(admin_client, "Kelp crisps", "Kelp crisps"))["id"])
    large = uuid.UUID((await make_product(admin_client, "Kelp snack", "Kelp crisps, large"))["id"])
    oats = uuid.UUID((await make_product(admin_client, "Oat rounds", "Oat rounds"))["id"])
    raws = [
        "KELP CRISPS 7,25",  # its alias is outvoted: returns to To identify
        "KELP CRISPS 7.25",  # resolved by the winning alias: unchanged
        "KELP CRISPS 6,10",  # a person chose: keeps the choice, takes the new key
        "FIG SPREAD 3,40",
        "FIG SPREAD 3.40",
        "OAT ROUNDS",
    ]
    purchase_id = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": r, "line_total": "1.00"} for r in raws]
    )

    run_alembic("downgrade", "0029")
    await _engine_reset()
    try:
        await owner_conn.execute(
            "UPDATE purchase SET status = 'committed' WHERE id = $1", uuid.UUID(purchase_id)
        )
        for raw in raws:
            await owner_conn.execute(
                "UPDATE purchase_line SET raw_text_norm = $1 WHERE raw_text = $2",
                normalize_v1(raw),
                raw,
            )
        decided = {
            "KELP CRISPS 7,25": ("alias", large, None),
            "KELP CRISPS 7.25": ("alias", kelp, None),
            "KELP CRISPS 6,10": ("manual", large, admin.id),
        }
        for raw, (resolution, product, by) in decided.items():
            await owner_conn.execute(
                "UPDATE purchase_line SET resolution = $1, product_id = $2, resolved_by = $3, "
                "flags = ARRAY['price_outlier'] WHERE raw_text = $4",
                resolution,
                product,
                by,
                raw,
            )
        ids = {name: uuid.uuid4() for name in ("kelp", "kelp_725", "kelp_610", "oats", "plum")}
        seeded = [
            (ids["kelp"], "KELP CRISPS", kelp, 3, 1, 0),
            (ids["kelp_725"], "KELP CRISPS 7 25", large, 1, 2, 1),
            (ids["kelp_610"], "KELP CRISPS 6 10", kelp, 2, 5, 2),
            (ids["oats"], "OAT ROUNDS", oats, 1, 0, 0),
            (ids["plum"], "PLUM BAR 2 10", oats, 1, 0, 0),  # no lines: keeps its key
        ]
        for alias_id, key, product, count, seen, created in seeded:
            await owner_conn.execute(
                "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, "
                "product_id, confirmed_count, last_seen_at, created_at) "
                "VALUES ($1, $2, $3, 'product', $4, $5, $6, $7)",
                alias_id,
                vendor,
                key,
                product,
                count,
                T0 + timedelta(days=seen),
                T0 + timedelta(days=created),
            )
        for key, name in (("FIG SPREAD 3 40", "Fig spread 3 40"), ("FIG SPREAD", "Fig spread")):
            await owner_conn.execute(
                "INSERT INTO naming_suggestion (id, vendor_id, raw_text_norm, status, name) "
                "VALUES ($1, $2, $3, 'done', $4)",
                uuid.uuid4(),
                vendor,
                key,
                name,
            )
        before_lines = await _lines(owner_conn)
        before_aliases = await _aliases(owner_conn)

        # The dry run counts and changes nothing.
        dry = subprocess.run(
            [sys.executable, "-c", CLI, "migrate", "--check-normalize"],
            cwd=BACKEND_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        counts = {
            label.strip(): int(n)
            for n, label in (ln.strip().split("  ", 1) for ln in dry.stdout.splitlines()[1:-1])
        }
        assert counts == {
            "lines re-keyed": 3,
            "aliases re-keyed": 0,
            "aliases merged": 1,
            "aliases outvoted": 1,
            "aliases dropped with no wording": 0,
            "lines returned to To identify": 1,
            "naming suggestions dropped": 1,
        }
        assert await _lines(owner_conn) == before_lines

        run_alembic("upgrade", "0030")
        lines = await _lines(owner_conn)
        assert {raw: lines[raw]["raw_text_norm"] for raw in raws} == {
            "KELP CRISPS 7,25": "KELP CRISPS",
            "KELP CRISPS 7.25": "KELP CRISPS",
            "KELP CRISPS 6,10": "KELP CRISPS",
            "FIG SPREAD 3,40": "FIG SPREAD",
            "FIG SPREAD 3.40": "FIG SPREAD",
            "OAT ROUNDS": "OAT ROUNDS",
        }
        returned = lines["KELP CRISPS 7,25"]
        assert (returned["resolution"], returned["product_id"]) == ("unmatched", None)
        assert returned["flags"] == []
        assert (
            lines["KELP CRISPS 7.25"]["resolution"],
            lines["KELP CRISPS 7.25"]["product_id"],
        ) == (
            "alias",
            kelp,
        )
        assert lines["KELP CRISPS 6,10"]["product_id"] == large  # a person's choice stays

        aliases = await _aliases(owner_conn)
        assert set(aliases) == {ids["kelp"], ids["oats"], ids["plum"]}
        kept = aliases[ids["kelp"]]
        assert (kept["raw_text_norm"], kept["product_id"], kept["confirmed_count"]) == (
            "KELP CRISPS",
            kelp,
            5,
        )
        assert kept["last_seen_at"] == T0 + timedelta(days=5)
        assert aliases[ids["plum"]]["raw_text_norm"] == "PLUM BAR 2 10"
        keys = await owner_conn.fetch("SELECT raw_text_norm FROM naming_suggestion")
        assert [r["raw_text_norm"] for r in keys] == ["FIG SPREAD"]

        run_alembic("downgrade", "0029")
        assert await _lines(owner_conn) == before_lines
        assert await _aliases(owner_conn) == before_aliases
        keys = await owner_conn.fetch("SELECT raw_text_norm FROM naming_suggestion")
        assert sorted(r["raw_text_norm"] for r in keys) == ["FIG SPREAD", "FIG SPREAD 3 40"]
        backup = await owner_conn.fetchval("SELECT to_regclass('normalize_v2_backup')")
        assert backup is None
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()

    # At head again (the same re-keying, run a second time on the restored rows):
    # a new line in either wording finds the alias, and the waiting group is one
    # group whose suggested name carries no price digits.
    from app.core.db import get_sessionmaker
    from app.services import naming, resolution

    later = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [{"raw_text": "KELP CRISPS 8,40", "line_total": "8.40"}],
        purchased_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    async with get_sessionmaker()() as db:
        await resolution.resolve_purchase(db, uuid.UUID(later))
    async with get_sessionmaker()() as db:
        rows = await naming.naming_rows(db)
    line_after = await owner_conn.fetchrow(
        "SELECT resolution, product_id FROM purchase_line WHERE raw_text = 'KELP CRISPS 8,40'"
    )
    assert (line_after["resolution"], line_after["product_id"]) == ("alias", kelp)
    fig = [r for r in rows if r["raw_text_norm"] == "FIG SPREAD"]
    assert len(fig) == 1 and fig[0]["line_count"] == 2
    assert all(not any(ch.isdigit() for ch in r["name"]) for r in rows)
