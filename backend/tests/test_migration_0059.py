"""Migration 0059 re-keys receipt wording with normalizer version 4, and back (#399).

The re-keying is 0030's, run with version 4, and 0030's tests cover its rules.
These check what is new: the frozen copy, that 0030's module is borrowed rather
than changed, and one round trip through the database at revision 0058 with
version 3 keys. Every vendor, product and wording here is invented.
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
from tests.normalize_v3 import normalize_v3
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


migration = _load("0059_normalize_v4.py", "migration_0059")
T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)
CLI = "from app.cli import cli; cli()"
V = uuid.uuid4()


@given(st.text(st.sampled_from(list("AB TX019.,@$/-*&%'LBOZM")), max_size=40))
def test_frozen_copy_matches_the_module_at_version_4(raw: str):
    # When the module moves on, this copy stays: the migration must not change.
    if NORMALIZE_VERSION == "4":
        assert migration.normalize_v4(raw) == normalize_receipt_text(raw)


def test_0030_is_borrowed_not_changed():
    v2 = _load("0030_normalize_v2.py", "migration_0030_check_0059")
    assert v2.BACKUP == "normalize_v2_backup"
    assert v2.normalize_v2("OAT MILK 1.5L") == "OAT MILK 1 5L"
    assert migration._rekey.BACKUP == "normalize_v4_backup"
    assert migration._rekey.normalize_v2("OAT MILK 1.5L") == "OAT MILK 1.5L"


def line(raw: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "vendor_id": V,
        "raw_text": raw,
        "raw_text_norm": normalize_v3(raw),
        "resolution": "unmatched",
        "auto": True,
        "product_id": None,
        "removed": False,
        **extra,
    }


def test_only_keys_with_a_point_between_digits_move():
    plain, sized = line("RVRBND BREAD FLR 2KG 6.50"), line("OAT MILK 1.5L 3.49")
    p = migration.plan([plain, sized], [], [])
    assert p.line_keys == {sized["id"]: "OAT MILK 1.5L"}


def test_a_line_with_no_text_keeps_its_key():
    keyed = line("OAT MILK 1.5L 3.49", raw_text=None)
    assert "COALESCE(pl.raw_text, pl.raw_text_norm) AS raw_text" in migration.LINES_SQL
    # As the query hands it over: the stored key stands in for the text.
    p = migration.plan([{**keyed, "raw_text": keyed["raw_text_norm"]}], [], [])
    assert p.line_keys == {}


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


async def test_keys_move_to_version_4_and_come_back(admin_client, admin, owner_conn):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    vendor = await owner_conn.fetchval(
        "SELECT vendor_id FROM vendor_location WHERE id = $1", uuid.UUID(loc["id"])
    )
    milk = uuid.UUID((await make_product(admin_client, "Oat milk", "Oat milk"))["id"])
    raws = ["OAT MILK 1.5L 3.49", "OAT MILK 1.5L", "OAT ROUNDS"]
    purchase_id = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": r, "line_total": "1.00"} for r in raws]
    )

    run_alembic("downgrade", "0058")
    await _engine_reset()
    try:
        await owner_conn.execute(
            "UPDATE purchase SET status = 'committed' WHERE id = $1", uuid.UUID(purchase_id)
        )
        for raw in raws:
            await owner_conn.execute(
                "UPDATE purchase_line SET raw_text_norm = $1 WHERE raw_text = $2",
                normalize_v3(raw),
                raw,
            )
        # The alias learned under the version 3 key moves to the key its lines now
        # carry, with its confirmations.
        alias = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, "
            "product_id, confirmed_count, last_seen_at, created_at) "
            "VALUES ($1, $2, 'OAT MILK 1 5L', 'product', $3, 2, $4, $4)",
            alias,
            vendor,
            milk,
            T0,
        )
        for key in ("OAT MILK 1 5L", "OAT ROUNDS"):
            await owner_conn.execute(
                "INSERT INTO naming_suggestion (id, vendor_id, raw_text_norm, status, name) "
                "VALUES ($1, $2, $3, 'done', $4)",
                uuid.uuid4(),
                vendor,
                key,
                key.capitalize(),
            )
        before_keys = await _keys(owner_conn)
        before_aliases = await _aliases(owner_conn)
        assert before_keys["OAT MILK 1.5L 3.49"] == "OAT MILK 1 5L"

        dry = subprocess.run(  # noqa: S603
            [sys.executable, "-c", CLI, "migrate", "--check-normalize"],
            cwd=BACKEND_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        assert dry.stdout.startswith("Migration 0059 would re-key")
        counts = {
            label.strip(): int(n)
            for n, label in (ln.strip().split("  ", 1) for ln in dry.stdout.splitlines()[1:-1])
        }
        assert counts == {
            "lines re-keyed": 2,
            "aliases re-keyed": 1,
            "aliases merged": 0,
            "aliases outvoted": 0,
            "aliases dropped with no wording": 0,
            "lines returned to To identify": 0,
            "naming suggestions dropped": 1,
        }
        assert await _keys(owner_conn) == before_keys

        run_alembic("upgrade", "0059")
        assert await _keys(owner_conn) == {
            "OAT MILK 1.5L 3.49": "OAT MILK 1.5L",
            "OAT MILK 1.5L": "OAT MILK 1.5L",
            "OAT ROUNDS": "OAT ROUNDS",
        }
        aliases = await _aliases(owner_conn)
        assert set(aliases) == {alias}
        assert (aliases[alias]["raw_text_norm"], aliases[alias]["confirmed_count"]) == (
            "OAT MILK 1.5L",
            2,
        )
        keys = await owner_conn.fetch("SELECT raw_text_norm FROM naming_suggestion")
        assert [r["raw_text_norm"] for r in keys] == ["OAT ROUNDS"]

        run_alembic("downgrade", "0058")
        assert await _keys(owner_conn) == before_keys
        assert await _aliases(owner_conn) == before_aliases
        keys = await owner_conn.fetch("SELECT raw_text_norm FROM naming_suggestion")
        assert sorted(r["raw_text_norm"] for r in keys) == ["OAT MILK 1 5L", "OAT ROUNDS"]
        assert await owner_conn.fetchval("SELECT to_regclass('normalize_v4_backup')") is None
        # 0049's own backup is not touched by 0059 either way.
        assert await owner_conn.fetchval("SELECT to_regclass('normalize_v3_backup')") is not None
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()
