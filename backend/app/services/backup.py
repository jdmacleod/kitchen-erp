"""Backup and restore: a consistent database dump plus the receipt images and photos.

`kerp backup --out DIR` writes `db.dump` (pg_dump custom format, owner role),
copies every receipt image under `receipts/` and every product photo original
and mask under `media/` (derivatives are rebuildable, so they are left out),
and writes `manifest.json` with hashes and counts. `kerp restore --from DIR`
refuses a non-empty database unless forced, restores the dump, copies the files
back, and verifies hashes: a receipt that does not match stops the restore, and
photo files that do not match are reported.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.grants import statements as grant_statements
from app.services.health import expected_migration_head

# The photo files a backup keeps (1I); derived/ and incoming/ are not kept.
MEDIA_KEPT = ("originals", "masks")

COUNT_TABLES = (
    "product_image",
    "purchase",
    "purchase_line",
    "price_observation",
    "receipt_alias",
    "receipt_document",
)


def libpq_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def media_files(root: Path) -> list[Path]:
    return [p for kept in MEDIA_KEPT for p in receipt_files(root / kept)]


def _copy_tree(files: list[Path], root: Path, dest: Path) -> list[dict[str, Any]]:
    copied = []
    for src in files:
        rel = src.relative_to(root)
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append({"path": str(rel), "sha256": sha256_of(src), "bytes": src.stat().st_size})
    return copied


def receipt_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


async def table_counts(db: AsyncSession) -> dict[str, int]:
    """Row counts of the tables this schema has. A database older than the code (the
    backup taken before a migration) lacks the newer tables; they are left out."""
    counts = {}
    for table in COUNT_TABLES:
        exists = (await db.execute(text("SELECT to_regclass(:t)"), {"t": table})).scalar_one()
        if exists is None:
            continue
        counts[table] = int((await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
    return counts


async def database_revision(db: AsyncSession) -> str | None:
    """The migration the database is at, which is not always the code's head."""
    if (await db.execute(text("SELECT to_regclass('alembic_version')"))).scalar_one() is None:
        return None
    return (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()


async def backup(db: AsyncSession, out: Path) -> dict[str, Any]:
    settings = get_settings()
    out.mkdir(parents=True, exist_ok=True)
    dump = out / "db.dump"
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            # PostGIS repopulates its reference table when the extension is created.
            "--exclude-table-data=spatial_ref_sys",
            "--file",
            str(dump),
            libpq_dsn(settings.migration_database_url),
        ],
        check=True,
        capture_output=True,
    )
    receipts_root = Path(settings.receipts_path)
    copied = _copy_tree(receipt_files(receipts_root), receipts_root, out / "receipts")
    media_root = Path(settings.media_path)
    photos = _copy_tree(media_files(media_root), media_root, out / "media")
    manifest = {
        "format": "kitchen-erp-backup/1",
        "created_at": datetime.now(UTC).isoformat(),
        # The dump's own revision; the code's head is kept beside it.
        "migration_head": await database_revision(db),
        "code_head": expected_migration_head(),
        "dump": {"file": "db.dump", "sha256": sha256_of(dump), "bytes": dump.stat().st_size},
        "receipts": copied,
        "media": photos,
        "counts": await table_counts(db),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


async def database_is_empty(db: AsyncSession) -> bool:
    counts = await table_counts(db)
    users = int((await db.execute(text("SELECT count(*) FROM app_user"))).scalar_one())
    ingredients = int((await db.execute(text("SELECT count(*) FROM ingredient"))).scalar_one())
    return sum(counts.values()) + users + ingredients == 0


async def _as_owner(statements: tuple[str, ...]) -> None:
    """Run DDL as the owner role on a dedicated connection (AUTOCOMMIT)."""
    engine = create_async_engine(get_settings().migration_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def reapply_grants() -> None:
    """The dump carries no privileges; the runtime role's grants are re-applied."""
    await _as_owner(grant_statements())


async def restore(db: AsyncSession, src: Path, *, force: bool = False) -> dict[str, Any]:
    settings = get_settings()
    manifest_path = src / "manifest.json"
    if not manifest_path.is_file():
        raise ApiError(400, "bad_backup", "No manifest.json in the backup directory.")
    manifest = json.loads(manifest_path.read_text())
    dump = src / manifest["dump"]["file"]
    if sha256_of(dump) != manifest["dump"]["sha256"]:
        raise ApiError(400, "bad_backup", "The database dump does not match its manifest hash.")
    if not force and not await database_is_empty(db):
        raise ApiError(
            409,
            "database_not_empty",
            "Restore refuses a non-empty database; pass --force to overwrite.",
        )
    # Start from a bare schema: pg_restore's --clean cannot drop objects owned by
    # the PostGIS extension, and the dump recreates the extension itself.
    await db.commit()
    await db.close()
    await _as_owner(("DROP SCHEMA public CASCADE", "CREATE SCHEMA public"))
    result = subprocess.run(
        [
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            "--dbname",
            libpq_dsn(settings.migration_database_url),
            str(dump),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ApiError(
            500, "restore_failed", "pg_restore failed.", {"stderr": result.stderr[-2000:]}
        )
    await reapply_grants()
    receipts_root = Path(settings.receipts_path)
    verified = 0
    for entry in manifest["receipts"]:
        source = src / "receipts" / entry["path"]
        target = receipts_root / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256_of(target) != entry["sha256"]:
            raise ApiError(500, "restore_failed", f"Hash mismatch for {entry['path']}.")
        verified += 1
    media_root = Path(settings.media_path)
    restored_media, mismatches = 0, []
    # A backup made before product photos has no "media" entry.
    for entry in manifest.get("media", []):
        source = src / "media" / entry["path"]
        target = media_root / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copy2(source, target)
        if not target.is_file() or sha256_of(target) != entry["sha256"]:
            mismatches.append(entry["path"])
            continue
        restored_media += 1
    return {
        "restored_receipts": verified,
        "restored_media": restored_media,
        "media_mismatches": mismatches,
        "counts": manifest["counts"],
    }
