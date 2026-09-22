"""Backup and restore round trip. Needs pg_dump/pg_restore (present in the api image)."""

import shutil
from pathlib import Path

import asyncpg
import pytest

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.services.backup import backup, restore
from tests.pricebook_helpers import make_location, make_product, shelf

needs_pg_tools = pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
    reason="pg_dump/pg_restore not installed on this host; runs inside the api container",
)

TABLES = ("purchase", "price_observation", "receipt_alias", "ingredient", "product")


@needs_pg_tools
async def test_backup_and_restore_round_trip(
    admin_client, owner_conn: asyncpg.Connection, tmp_path: Path, monkeypatch
):
    receipts = tmp_path / "receipts"
    (receipts / "ab" / "cd").mkdir(parents=True)
    image = receipts / "ab" / "cd" / "deadbeef.png"
    image.write_bytes(b"\x89PNG synthetic bytes")
    monkeypatch.setattr(get_settings(), "receipts_path", str(receipts))

    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    await shelf(admin_client, box["id"], loc["id"], "3.99")
    before = {t: await owner_conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}
    assert before["price_observation"] == 1

    out = tmp_path / "backup"
    async with get_sessionmaker()() as db:
        manifest = await backup(db, out)
    assert (out / "db.dump").is_file() and (out / "manifest.json").is_file()
    assert manifest["receipts"][0]["path"] == "ab/cd/deadbeef.png"
    assert manifest["counts"]["price_observation"] == 1

    # Refuses a non-empty database unless forced.
    async with get_sessionmaker()() as db:
        with pytest.raises(Exception, match="non-empty"):
            await restore(db, out)
    await dispose_engine()

    # Wipe and restore.
    await owner_conn.execute("TRUNCATE app_user, ingredient, vendor, place CASCADE")
    image.unlink()
    async with get_sessionmaker()() as db:
        result = await restore(db, out)
    await dispose_engine()
    assert result["restored_receipts"] == 1
    after = {t: await owner_conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}
    assert after == before
    assert image.read_bytes() == b"\x89PNG synthetic bytes"
