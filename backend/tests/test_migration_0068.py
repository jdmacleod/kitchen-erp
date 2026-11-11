"""Migration 0068 re-keys receipt wording with normalizer version 5, and back (#489).

The re-keying is 0030's, run with version 5, and 0030's tests cover its rules.
These check what is new: the frozen copy, that 0030's module is borrowed rather
than changed, the product proposals' keys, and one round trip through the
database at revision 0067 with version 4 keys. Every vendor, product and wording
here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from hypothesis import given
from hypothesis import strategies as st

from app.services.normalize import NORMALIZE_VERSION, normalize_receipt_text
from tests.conftest import BACKEND_ROOT, run_alembic
from tests.normalize_v4 import normalize_v4
from tests.pricebook_helpers import make_location, make_product
from tests.resolution_helpers import make_receipt_purchase


def _load(filename: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        name, BACKEND_ROOT / Path("alembic/versions") / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load("0068_normalize_v5.py", "migration_0068")
T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)
CLI = "from app.cli import cli; cli()"
V = uuid.uuid4()
WORDS = st.lists(
    st.sampled_from(
        [
            "KELP",
            "OAT",
            "1234",
            "F",
            "T",
            "-",
            "1.60",
            "$0.80",
            "REGULAR",
            "REG.",
            "PRICE",
            "YOU",
            "SAVED",
            "SAVE",
        ]
    ),
    max_size=10,
).map(" ".join)


@given(st.one_of(WORDS, st.text(st.sampled_from(list("AB TX019.,@$/-*&%'LBOZM")), max_size=40)))
def test_frozen_copy_matches_the_module_at_version_5(raw: str):
    # When the module moves on, this copy stays: the migration must not change.
    if NORMALIZE_VERSION == "5":
        assert migration.normalize_v5(raw) == normalize_receipt_text(raw)


def test_0030_is_borrowed_not_changed():
    v2 = _load("0030_normalize_v2.py", "migration_0030_check_0068")
    assert v2.BACKUP == "normalize_v2_backup"
    assert v2.normalize_v2("KELP REGULAR PRICE") == "KELP REGULAR PRICE"
    assert migration._rekey.BACKUP == "normalize_v5_backup"
    assert migration._rekey.normalize_v2("KELP REGULAR PRICE") == "KELP"


def line(raw: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "vendor_id": V,
        "raw_text": raw,
        "raw_text_norm": normalize_v4(raw),
        "resolution": "unmatched",
        "auto": True,
        "product_id": None,
        "removed": False,
        **extra,
    }


def test_only_keys_with_a_saving_phrase_move():
    plain, folded = line("KELP CRISPS 1.60 F"), line("KELP CRISPS 1.60 F Regular Price 2.40")
    p = migration.plan([plain, folded], [], [])
    assert p.line_keys == {folded["id"]: "KELP CRISPS"}


def test_a_line_with_no_text_moves_from_its_key():
    keyed = line("KELP CRISPS 1.60 F You saved 0.80", raw_text=None)
    assert "COALESCE(pl.raw_text, pl.raw_text_norm) AS raw_text" in migration.LINES_SQL
    p = migration.plan([{**keyed, "raw_text": keyed["raw_text_norm"]}], [], [])
    assert p.line_keys == {keyed["id"]: "KELP CRISPS"}


def test_proposals_move_from_their_key():
    moving, staying = uuid.uuid4(), uuid.uuid4()
    moved = migration.plan_proposals(
        [
            {"id": moving, "for_raw_text_norm": "KELP CRISPS F REGULAR PRICE YOU SAVED"},
            {"id": staying, "for_raw_text_norm": "KELP CRISPS"},
        ]
    )
    assert moved == {moving: "KELP CRISPS"}


def alias(key: str, disposition: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "vendor_id": V,
        "raw_text_norm": key,
        "disposition": disposition,
        "product_id": None,
        "supply_category": None,
        "confirmed_count": 1,
        "last_seen_at": T0,
        "created_at": T0,
        **extra,
    }


def test_supply_aliases_of_different_categories_are_different_decisions():
    folded = line("FOIL ROLL 3.00 T Regular Price 4.00", resolution="supply")
    folded["supply_category"] = "wraps"
    wraps = alias(folded["raw_text_norm"], "supply", supply_category="wraps")
    storage = alias("FOIL ROLL", "supply", supply_category="storage", confirmed_count=3)
    p = migration.plan([folded], [wraps, storage], [])
    # Outvoted, not merged: the category with more confirmations stays, and
    # the line the losing alias marked on its own goes back to be identified.
    assert p.aliases_outvoted == 1 and p.aliases_merged == 0
    assert wraps["id"] in p.alias_deletes and storage["id"] not in p.alias_deletes
    assert p.lines_returned == [folded["id"]]


def test_supply_aliases_of_one_category_merge():
    folded = line("FOIL ROLL 3.00 T Regular Price 4.00")
    one = alias(folded["raw_text_norm"], "supply", supply_category="wraps")
    other = alias("FOIL ROLL", "supply", supply_category="wraps", created_at=T0.replace(day=2))
    p = migration.plan([folded], [one, other], [])
    assert p.aliases_merged == 1 and p.aliases_outvoted == 0


# --- the round trip ----------------------------------------------------------------------


async def _engine_reset() -> None:
    from app.core.db import dispose_engine

    await dispose_engine()


async def _aliases(conn: asyncpg.Connection) -> dict[uuid.UUID, dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT id, raw_text_norm, product_id, confirmed_count, last_seen_at FROM receipt_alias"
    )
    return {r["id"]: dict(r) for r in rows}


async def _keys(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT raw_text, raw_text_norm FROM purchase_line")
    return {r["raw_text"]: r["raw_text_norm"] for r in rows}


async def _proposal_key(conn: asyncpg.Connection, proposal_id: uuid.UUID) -> str:
    return await conn.fetchval(
        "SELECT for_raw_text_norm FROM product_proposal WHERE id = $1", proposal_id
    )


async def test_keys_move_to_version_5_and_come_back(admin_client, admin, owner_conn):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    vendor = await owner_conn.fetchval(
        "SELECT vendor_id FROM vendor_location WHERE id = $1", uuid.UUID(loc["id"])
    )
    crisps = uuid.UUID((await make_product(admin_client, "Kelp crisps", "Kelp crisps"))["id"])
    folded = "KELP CRISPS 1.60 F Regular Price 2.40 , You saved 0.80"
    raws = [folded, "KELP CRISPS 1.60 F", "OAT ROUNDS"]
    purchase_id = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": r, "line_total": "1.00"} for r in raws]
    )

    run_alembic("downgrade", "0067")
    await _engine_reset()
    try:
        await owner_conn.execute(
            "UPDATE purchase SET status = 'committed' WHERE id = $1", uuid.UUID(purchase_id)
        )
        for raw in raws:
            await owner_conn.execute(
                "UPDATE purchase_line SET raw_text_norm = $1 WHERE raw_text = $2",
                normalize_v4(raw),
                raw,
            )
        # The alias the plain lines taught stays; the folded line now meets it.
        alias = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, "
            "product_id, confirmed_count, last_seen_at, created_at) "
            "VALUES ($1, $2, 'KELP CRISPS', 'product', $3, 3, $4, $4)",
            alias,
            vendor,
            crisps,
            T0,
        )
        await owner_conn.execute(
            "INSERT INTO naming_suggestion (id, vendor_id, raw_text_norm, status, name) "
            "VALUES ($1, $2, $3, 'done', 'Kelp crisps')",
            uuid.uuid4(),
            vendor,
            normalize_v4(folded),
        )
        proposal = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO product_proposal (id, kind, status, fields, match, "
            "for_vendor_id, for_raw_text_norm) "
            "VALUES ($1, 'new_product', 'pending', '{}', '{}', $2, $3)",
            proposal,
            vendor,
            normalize_v4(folded),
        )
        before_keys = await _keys(owner_conn)
        before_aliases = await _aliases(owner_conn)
        assert before_keys[folded] == "KELP CRISPS F REGULAR PRICE YOU SAVED"

        dry = subprocess.run(  # noqa: S603
            [sys.executable, "-c", CLI, "migrate", "--check-normalize"],
            cwd=BACKEND_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        assert dry.stdout.startswith("Migration 0068 would re-key")
        counts = {
            label.strip(): int(n)
            for n, label in (ln.strip().split("  ", 1) for ln in dry.stdout.splitlines()[1:-1])
        }
        assert counts == {
            "lines re-keyed": 1,
            "aliases re-keyed": 0,
            "aliases merged": 0,
            "aliases outvoted": 0,
            "aliases dropped with no wording": 0,
            "lines returned to To identify": 0,
            "naming suggestions dropped": 1,
            "product proposals re-keyed": 1,
        }
        assert await _keys(owner_conn) == before_keys

        run_alembic("upgrade", "0068")
        assert await _keys(owner_conn) == {
            folded: "KELP CRISPS",
            "KELP CRISPS 1.60 F": "KELP CRISPS",
            "OAT ROUNDS": "OAT ROUNDS",
        }
        assert await _aliases(owner_conn) == before_aliases
        assert await owner_conn.fetchval("SELECT count(*) FROM naming_suggestion") == 0
        assert await _proposal_key(owner_conn, proposal) == "KELP CRISPS"

        run_alembic("downgrade", "0067")
        assert await _keys(owner_conn) == before_keys
        assert await _aliases(owner_conn) == before_aliases
        assert await owner_conn.fetchval("SELECT count(*) FROM naming_suggestion") == 1
        assert await _proposal_key(owner_conn, proposal) == normalize_v4(folded)
        assert await owner_conn.fetchval("SELECT to_regclass('normalize_v5_backup')") is None
        assert await owner_conn.fetchval("SELECT to_regclass('normalize_v4_backup')") is not None
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()


async def test_an_outvoted_supply_line_loses_its_category_and_gets_it_back(
    admin_client, admin, owner_conn
):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    vendor = await owner_conn.fetchval(
        "SELECT vendor_id FROM vendor_location WHERE id = $1", uuid.UUID(loc["id"])
    )
    foil = uuid.UUID((await make_product(admin_client, "Foil roll", "Foil roll"))["id"])
    folded = "FOIL ROLL 3.00 T Regular Price 4.00"
    await make_receipt_purchase(admin.id, loc["id"], [{"raw_text": folded, "line_total": "3.00"}])

    run_alembic("downgrade", "0067")
    await _engine_reset()
    try:
        old_key = normalize_v4(folded)
        await owner_conn.execute(
            "UPDATE purchase_line SET raw_text_norm = $1, resolution = 'supply', "
            "supply_category = 'wraps', product_id = NULL, resolved_by = NULL",
            old_key,
        )
        for key, disposition, product, category, count in (
            (old_key, "supply", None, "wraps", 1),
            ("FOIL ROLL", "product", foil, None, 4),
        ):
            await owner_conn.execute(
                "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, "
                "product_id, supply_category, confirmed_count, last_seen_at, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)",
                uuid.uuid4(),
                vendor,
                key,
                disposition,
                product,
                category,
                count,
                T0,
            )
        before = dict(
            await owner_conn.fetchrow(
                "SELECT raw_text_norm, resolution, supply_category FROM purchase_line"
            )
        )

        run_alembic("upgrade", "0068")
        after = await owner_conn.fetchrow(
            "SELECT raw_text_norm, resolution, supply_category FROM purchase_line"
        )
        assert dict(after) == {
            "raw_text_norm": "FOIL ROLL",
            "resolution": "unmatched",
            "supply_category": None,
        }
        assert await owner_conn.fetchval("SELECT count(*) FROM receipt_alias") == 1

        run_alembic("downgrade", "0067")
        restored = await owner_conn.fetchrow(
            "SELECT raw_text_norm, resolution, supply_category FROM purchase_line"
        )
        assert dict(restored) == before
        assert await owner_conn.fetchval("SELECT count(*) FROM receipt_alias") == 2
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()


async def test_the_dry_run_reads_a_database_from_before_proposal_keys(owner_conn):
    run_alembic("downgrade", "0064")
    await _engine_reset()
    try:
        dry = subprocess.run(  # noqa: S603
            [sys.executable, "-c", CLI, "migrate", "--check-normalize"],
            cwd=BACKEND_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        assert dry.stdout.startswith("Migration 0068 would re-key"), dry.stdout
        assert "0  product proposals re-keyed" in dry.stdout
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()
