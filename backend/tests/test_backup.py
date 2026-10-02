"""Backup and restore round trip. Needs pg_dump/pg_restore (present in the api image)."""

import shutil
from pathlib import Path

import asyncpg
import pytest

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.services.backup import backup, restore
from tests.grants_helpers import privileges
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
    # Photo files (1I): originals and masks are kept; derived/ and incoming/ are not.
    media_root = tmp_path / "media"
    photo_files = {
        "originals/aa/bb/original.jpg": b"\xff\xd8\xff synthetic original",
        "masks/cc/dd/mask.png": b"\x89PNG synthetic mask",
    }
    for rel, data in photo_files.items():
        (media_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (media_root / rel).write_bytes(data)
    (media_root / "derived" / "aa").mkdir(parents=True)
    (media_root / "derived" / "aa" / "480-x.webp").write_bytes(b"RIFF rebuildable")
    monkeypatch.setattr(get_settings(), "media_path", str(media_root))

    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    await shelf(admin_client, box["id"], loc["id"], "3.99")
    # A purchase with a removed line (#72): the mark must survive the round trip.
    line = {"product_id": box["id"], "qty": "1", "unit": "each", "line_total": "3.49"}
    body = {"vendor_location_id": loc["id"], "purchased_at": "2026-06-01T12:00:00Z"}
    p = (await admin_client.post("/api/v1/purchases", json={**body, "lines": [line, line]})).json()
    kept = {**line, "id": p["lines"][0]["id"]}
    r = await admin_client.put(f"/api/v1/purchases/{p['id']}", json={**body, "lines": [kept]})
    assert r.status_code == 200, r.text
    removed_sql = "SELECT count(*) FROM purchase_line WHERE removed_at IS NOT NULL"
    before = {t: await owner_conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}
    granted = await privileges(owner_conn)
    assert before["price_observation"] == 3

    out = tmp_path / "backup"
    async with get_sessionmaker()() as db:
        manifest = await backup(db, out)
    assert (out / "db.dump").is_file() and (out / "manifest.json").is_file()
    assert manifest["receipts"][0]["path"] == "ab/cd/deadbeef.png"
    assert manifest["counts"]["price_observation"] == 3
    assert sorted(m["path"] for m in manifest["media"]) == sorted(photo_files)
    assert not (out / "media" / "derived").exists()

    # Refuses a non-empty database unless forced.
    async with get_sessionmaker()() as db:
        with pytest.raises(Exception, match="non-empty"):
            await restore(db, out)
    await dispose_engine()

    # Wipe and restore.
    await owner_conn.execute("TRUNCATE app_user, ingredient, vendor, place CASCADE")
    image.unlink()
    shutil.rmtree(media_root)
    async with get_sessionmaker()() as db:
        result = await restore(db, out)
    await dispose_engine()
    assert result["restored_receipts"] == 1
    assert result["restored_media"] == 2 and result["media_mismatches"] == []
    assert {rel: (media_root / rel).read_bytes() for rel in photo_files} == photo_files
    after = {t: await owner_conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}
    assert after == before
    # The dump carries no privileges; the runtime role gets back exactly what it had.
    assert await privileges(owner_conn) == granted
    assert await owner_conn.fetchval(removed_sql) == 1
    assert image.read_bytes() == b"\x89PNG synthetic bytes"


@needs_pg_tools
async def test_restore_reports_a_photo_file_that_does_not_match(
    admin_client, owner_conn: asyncpg.Connection, tmp_path: Path, monkeypatch
):
    """Criterion 101: restore verifies each photo file and reports a mismatch."""
    monkeypatch.setattr(get_settings(), "receipts_path", str(tmp_path / "receipts"))
    media_root = tmp_path / "media"
    original = media_root / "originals" / "aa" / "bb" / "original.jpg"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"\xff\xd8\xff synthetic original")
    monkeypatch.setattr(get_settings(), "media_path", str(media_root))

    out = tmp_path / "backup"
    async with get_sessionmaker()() as db:
        await backup(db, out)
    (out / "media" / "originals" / "aa" / "bb" / "original.jpg").write_bytes(b"altered")
    async with get_sessionmaker()() as db:
        result = await restore(db, out, force=True)
    await dispose_engine()
    assert result["media_mismatches"] == ["originals/aa/bb/original.jpg"]
    assert result["restored_media"] == 0
