"""Backup and restore: a consistent database dump plus the receipt images.

`kerp backup --out DIR` writes `db.dump` (pg_dump custom format, owner role),
copies every receipt image under `receipts/`, and writes `manifest.json` with
hashes and counts. `kerp restore --from DIR` refuses a non-empty database
unless forced, restores the dump, copies the images back, and verifies hashes.
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
from app.services.health import expected_migration_head

COUNT_TABLES = (
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


def receipt_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


async def table_counts(db: AsyncSession) -> dict[str, int]:
    counts = {}
    for table in COUNT_TABLES:
        counts[table] = int((await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
    return counts


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
    copied = []
    for src in receipt_files(receipts_root):
        rel = src.relative_to(receipts_root)
        dst = out / "receipts" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append({"path": str(rel), "sha256": sha256_of(src), "bytes": src.stat().st_size})
    manifest = {
        "format": "kitchen-erp-backup/1",
        "created_at": datetime.now(UTC).isoformat(),
        "migration_head": expected_migration_head(),
        "dump": {"file": "db.dump", "sha256": sha256_of(dump), "bytes": dump.stat().st_size},
        "receipts": copied,
        "counts": await table_counts(db),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


async def database_is_empty(db: AsyncSession) -> bool:
    counts = await table_counts(db)
    users = int((await db.execute(text("SELECT count(*) FROM app_user"))).scalar_one())
    ingredients = int((await db.execute(text("SELECT count(*) FROM ingredient"))).scalar_one())
    return sum(counts.values()) + users + ingredients == 0


GRANTS = (
    "GRANT USAGE ON SCHEMA public TO kerp_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO kerp_app",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO kerp_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kerp_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO kerp_app",
    "REVOKE UPDATE, DELETE ON price_observation, price_observation_void, ingest_stage_result "
    "FROM kerp_app",
    "GRANT TRUNCATE ON price_norm TO kerp_app",
)


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
    await _as_owner(GRANTS)


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
    return {"restored_receipts": verified, "counts": manifest["counts"]}
