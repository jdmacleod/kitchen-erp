"""Migration 0049 re-keys receipt wording with normalizer version 3, and back (#258).

The re-keying is 0030's, run with version 3, and 0030's tests cover its rules.
These check what is new: the frozen copy, that 0030's module is borrowed rather
than changed, and one round trip through the database at revision 0048 with
version 2 keys. Every vendor, product and wording here is invented.
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
from tests.normalize_v2 import normalize_v2
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


migration = _load("0049_normalize_v3.py", "migration_0049")
T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)
CLI = "from app.cli import cli; cli()"
V = uuid.uuid4()


@given(st.text(st.sampled_from(list("AB TX019.,@$/-*&%'LBOZ")), max_size=40))
def test_frozen_copy_matches_the_module_at_version_3(raw: str):
    # When the module moves on, this copy stays: the migration must not change.
    if NORMALIZE_VERSION == "3":
        assert migration.normalize_v3(raw) == normalize_receipt_text(raw)


def test_0030_is_borrowed_not_changed():
    v2 = _load("0030_normalize_v2.py", "migration_0030_check")
    assert v2.BACKUP == "normalize_v2_backup"
    assert v2.normalize_v2("0.B-") == "0 B"
    assert migration._rekey.BACKUP == "normalize_v3_backup"
    assert migration._rekey.normalize_v2("0.B-") == "0"


def line(raw: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "vendor_id": V,
        "raw_text": raw,
        "raw_text_norm": normalize_v2(raw),
        "resolution": "unmatched",
        "auto": True,
        "product_id": None,
        "removed": False,
        **extra,
    }


def test_only_unfinished_keys_move():
    done, unfinished = line("SEA SALT FLAKES"), line("SEA SALT B -")
    p = migration.plan([done, unfinished], [], [])
    assert p.line_keys == {unfinished["id"]: "SEA SALT"}


def test_a_line_with_no_text_is_finished_from_its_key():
    keyed = line("SEA SALT B -", raw_text=None)
    sql_line = migration.LINES_SQL
    assert "COALESCE(pl.raw_text, pl.raw_text_norm) AS raw_text" in sql_line
    # As the query hands it over: the stored key stands in for the text.
    p = migration.plan([{**keyed, "raw_text": keyed["raw_text_norm"]}], [], [])
    assert p.line_keys == {keyed["id"]: "SEA SALT"}


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


async def test_keys_move_to_version_3_and_come_back(admin_client, admin, owner_conn):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    vendor = await owner_conn.fetchval(
        "SELECT vendor_id FROM vendor_location WHERE id = $1", uuid.UUID(loc["id"])
    )
    salt = uuid.UUID((await make_product(admin_client, "Sea salt", "Sea salt flakes"))["id"])
    raws = ["SEA SALT B -", "SEA SALT", "OAT ROUNDS"]
    purchase_id = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": r, "line_total": "1.00"} for r in raws]
    )

    run_alembic("downgrade", "0048")
    await _engine_reset()
    try:
        await owner_conn.execute(
            "UPDATE purchase SET status = 'committed' WHERE id = $1", uuid.UUID(purchase_id)
        )
        for raw in raws:
            await owner_conn.execute(
                "UPDATE purchase_line SET raw_text_norm = $1 WHERE raw_text = $2",
                normalize_v2(raw),
                raw,
            )
        # Two aliases making the same decision come to share a key: the older is
        # re-keyed and keeps both confirmations.
        older, newer = uuid.uuid4(), uuid.uuid4()
        for alias_id, key, count, days in ((older, "SEA SALT B", 1, 0), (newer, "SEA SALT", 2, 3)):
            await owner_conn.execute(
                "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, "
                "product_id, confirmed_count, last_seen_at, created_at) "
                "VALUES ($1, $2, $3, 'product', $4, $5, $6, $6)",
                alias_id,
                vendor,
                key,
                salt,
                count,
                T0 + timedelta(days=days),
            )
        for key in ("SEA SALT B", "OAT ROUNDS"):
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
        assert before_keys["SEA SALT B -"] == "SEA SALT B"

        dry = subprocess.run(
            [sys.executable, "-c", CLI, "migrate", "--check-normalize"],
            cwd=BACKEND_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        assert dry.stdout.startswith("Migration 0049 would re-key")
        counts = {
            label.strip(): int(n)
            for n, label in (ln.strip().split("  ", 1) for ln in dry.stdout.splitlines()[1:-1])
        }
        assert counts == {
            "lines re-keyed": 1,
            "aliases re-keyed": 1,
            "aliases merged": 1,
            "aliases outvoted": 0,
            "aliases dropped with no wording": 0,
            "lines returned to To identify": 0,
            "naming suggestions dropped": 1,
        }
        assert await _keys(owner_conn) == before_keys

        run_alembic("upgrade", "0049")
        assert await _keys(owner_conn) == {
            "SEA SALT B -": "SEA SALT",
            "SEA SALT": "SEA SALT",
            "OAT ROUNDS": "OAT ROUNDS",
        }
        aliases = await _aliases(owner_conn)
        assert set(aliases) == {older}
        assert (aliases[older]["raw_text_norm"], aliases[older]["confirmed_count"]) == (
            "SEA SALT",
            3,
        )
        keys = await owner_conn.fetch("SELECT raw_text_norm FROM naming_suggestion")
        assert [r["raw_text_norm"] for r in keys] == ["OAT ROUNDS"]

        run_alembic("downgrade", "0048")
        assert await _keys(owner_conn) == before_keys
        assert await _aliases(owner_conn) == before_aliases
        keys = await owner_conn.fetch("SELECT raw_text_norm FROM naming_suggestion")
        assert sorted(r["raw_text_norm"] for r in keys) == ["OAT ROUNDS", "SEA SALT B"]
        assert await owner_conn.fetchval("SELECT to_regclass('normalize_v3_backup')") is None
        # 0030's own backup is not touched by 0049 either way.
        assert await owner_conn.fetchval("SELECT to_regclass('normalize_v2_backup')") is not None
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()
