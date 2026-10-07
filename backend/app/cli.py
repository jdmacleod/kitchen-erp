"""The `kerp` command."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import typer
from alembic.config import Config

from alembic import command, util
from app.core.config import DEV_VERSION, UNKNOWN_COMMIT
from app.core.logging import configure_logging

cli = typer.Typer(help="Kitchen ERP administration.", no_args_is_help=True)
seed_cli = typer.Typer(help="Seed reference data.", no_args_is_help=True)
cli.add_typer(seed_cli, name="seed")
import_cli = typer.Typer(
    help="Import reference data or a retailer's purchase export.", no_args_is_help=True
)
cli.add_typer(import_cli, name="import")

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _version_line() -> str:
    """The build identity, read straight from the environment.

    Deliberately not through `Settings`: `database_url` and `migration_database_url`
    are declared without defaults, so constructing settings raises `ValidationError`
    when they are unset. A version check that needs a configured database is not a
    version check — the first thing an operator runs on a container that will not
    start is `kerp --version`.
    """
    version = os.environ.get("BUILD_VERSION", DEV_VERSION)
    commit = os.environ.get("BUILD_COMMIT", UNKNOWN_COMMIT)
    return f"kitchen-erp {version} ({commit})"


def _show_version(value: bool) -> None:
    if not value:
        return
    typer.echo(_version_line())
    raise typer.Exit()


@cli.callback()
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show the build version and commit, then exit.",
    ),
) -> None:
    """Kitchen ERP administration."""


def alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def _run_alembic(action, revision: str, verb: str) -> None:
    """Run an upgrade or downgrade and say what it did; alembic/env.py prints each step."""
    cfg = alembic_config()
    typer.echo(f"{verb.capitalize()} the database to {revision}…")
    try:
        action(cfg, revision)
    except util.CommandError as exc:
        # A mistyped revision is a usage error, not a crash: say so in one line.
        typer.echo(f"Error: {exc}. `kerp migrate` with no argument goes to the latest.", err=True)
        raise typer.Exit(2) from exc
    steps = cfg.attributes.get("steps", [])
    if not steps:
        typer.echo("Nothing to do: the database is already there.")
        return
    noun = "migration" if len(steps) == 1 else "migrations"
    # From the last step itself: asking the database again could fail after
    # the commit and make a finished migration look failed.
    typer.echo(f"Committed {len(steps)} {noun}. The database is at {cfg.attributes['at']}.")


@cli.command()
def migrate(
    revision: str = typer.Argument("head"),
    check_barcodes: bool = typer.Option(
        False,
        "--check-barcodes",
        help="Only count how migration 0017 will move product barcodes; change nothing.",
    ),
    check_normalize: bool = typer.Option(
        False,
        "--check-normalize",
        help="Only count how migration 0030 will re-key receipt wording; change nothing.",
    ),
) -> None:
    """Apply migrations as the owner role, then seed reference units (idempotent)."""
    if check_barcodes:
        _check_barcodes()
        return
    if check_normalize:
        _check_normalize()
        return
    _run_alembic(command.upgrade, revision, "migrating")
    if revision == "head":
        _seed_units()


def _check_barcodes() -> None:
    """Dry run of 0017's barcode move, using the migration's own frozen classifier."""
    import importlib.util

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    path = Path(__file__).resolve().parent.parent / "alembic/versions/0017_product_identity.py"
    spec = importlib.util.spec_from_file_location("migration_0017", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def _run() -> list[tuple]:
        engine = create_async_engine(get_settings().migration_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                has_column = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name = 'product' AND column_name = 'barcode'"
                        )
                    )
                ).first()
                if has_column is None:
                    return []
                rows = await conn.execute(
                    text(
                        "SELECT id, barcode, exclusive_vendor_id FROM product "
                        "WHERE barcode IS NOT NULL ORDER BY created_at, id"
                    )
                )
                return [tuple(r) for r in rows]
        finally:
            await engine.dispose()

    rows = asyncio.run(_run())
    if not rows:
        typer.echo("No product barcodes to move (already migrated, or none set).")
        return
    _, counts = migration.plan(rows)
    labels = {
        "gtin": "become barcodes (GTIN-14)",
        "plu": "become produce codes for the product's own vendor",
        "plu_without_vendor": "4-5 digits with no exclusive vendor: kept as other codes",
        "ambiguous_8_digit": "8 digits valid as EAN-8 and as UPC-E: kept as other codes",
        "duplicate_gtin": "the same barcode as an earlier product: kept as other codes",
        "other": "kept as other codes",
    }
    typer.echo(f"{len(rows)} product barcodes would move:")
    for outcome, label in labels.items():
        if counts.get(outcome):
            typer.echo(f"  {counts[outcome]:>5}  {label}")
    typer.echo("Nothing was changed. Run `kerp migrate` to apply.")


def _check_normalize() -> None:
    """Dry run of 0030's re-keying, using the migration's own frozen normalizer and plan."""
    import importlib.util

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    path = Path(__file__).resolve().parent.parent / "alembic/versions/0030_normalize_v2.py"
    spec = importlib.util.spec_from_file_location("migration_0030", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def _run() -> tuple[bool, list[list[dict]]]:
        engine = create_async_engine(get_settings().migration_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                applied = (
                    await conn.execute(text(f"SELECT to_regclass('{migration.BACKUP}')"))
                ).scalar() is not None
                rows = []
                for sql in (migration.LINES_SQL, migration.ALIASES_SQL, migration.SUGGESTIONS_SQL):
                    rows.append([dict(r) for r in (await conn.execute(text(sql))).mappings()])
                return applied, rows
        finally:
            await engine.dispose()

    applied, (lines, aliases, suggestions) = asyncio.run(_run())
    if applied:
        typer.echo("Migration 0030 has already run: receipt wording uses normalizer version 2.")
        return
    summary = migration.plan(lines, aliases, suggestions).summary()
    typer.echo("Migration 0030 would re-key receipt wording (normalizer version 2):")
    for label, n in summary.items():
        typer.echo(f"  {n:>5}  {label}")
    typer.echo("Nothing was changed. Run `kerp migrate` to apply.")


def _seed_units() -> None:
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.units import seed_units as _seed

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            n = await _seed(db)
        await dispose_engine()
        typer.echo(f"seeded {n} units")

    asyncio.run(_run())


@cli.command()
def downgrade(revision: str = typer.Argument("-1")) -> None:
    """Revert migrations as the owner role."""
    _run_alembic(command.downgrade, revision, "downgrading")


@cli.command("create-admin")
def create_admin(
    email: str | None = typer.Option(None, help="Prompted for when omitted."),
    display_name: str | None = typer.Option(None, help="Prompted for when omitted."),
    password: str | None = typer.Option(None, help="Prompted for (twice, hidden) when omitted."),
) -> None:
    """Create the first administrator (or another one)."""
    from pydantic import ValidationError

    from app.core.db import dispose_engine, get_sessionmaker
    from app.core.errors import ApiError
    from app.schemas.identity import UserCreate
    from app.services import identity

    if None in (email, display_name, password) and not sys.stdin.isatty():
        # `exec -T` and scripts have no terminal to prompt on; say what to pass
        # instead of click's bare "Aborted.".
        typer.echo(
            "error: no terminal to prompt on; pass --email, --display-name and --password",
            err=True,
        )
        raise typer.Exit(code=2)
    if email is None:
        email = typer.prompt("Email")
    if display_name is None:
        display_name = typer.prompt("Display name")
    if password is None:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)

    # The same rules as POST /users, so the CLI cannot create an account the API
    # would have refused (an address that is not one, a one-character password).
    try:
        fields = UserCreate(email=email, display_name=display_name, password=password)
    except ValidationError as exc:
        for problem in exc.errors():
            field = str(problem["loc"][0]).replace("_", "-")
            typer.echo(f"error: --{field}: {problem['msg']}", err=True)
        raise typer.Exit(code=2) from exc

    async def _run() -> None:
        try:
            async with get_sessionmaker()() as db:
                user = await identity.create_user(
                    db,
                    email=fields.email,
                    display_name=fields.display_name,
                    password=fields.password,
                    role="admin",
                )
                typer.echo(f"created admin {user.email} ({user.id})")
        finally:
            await dispose_engine()

    try:
        asyncio.run(_run())
    except ApiError as exc:
        typer.echo(f"error: {exc.message}", err=True)
        raise typer.Exit(code=1) from exc


@cli.command()
def worker() -> None:
    """Run the ingest worker."""
    from app.core.config import get_settings
    from app.core.db import WORKER_APPLICATION_NAME, set_application_name
    from app.worker import run

    configure_logging(get_settings().log_level)
    set_application_name(WORKER_APPLICATION_NAME)
    asyncio.run(run())


ingest_cli = typer.Typer(help="Receipt ingest.", no_args_is_help=True)
cli.add_typer(ingest_cli, name="ingest")


@ingest_cli.command("run-once")
def ingest_run_once(
    drain: bool = typer.Option(False, "--drain", help="Keep going until nothing is claimable."),
) -> None:
    """Claim and run one stage of one pending ingest job (or all, with --drain)."""
    from app.core.config import get_settings
    from app.core.db import dispose_engine, get_sessionmaker
    from app.worker import run_once

    configure_logging(get_settings().log_level)

    async def _run() -> None:
        n = 0
        async with get_sessionmaker()() as db:
            while await run_once(db):
                n += 1
                if not drain:
                    break
        await dispose_engine()
        typer.echo(f"ran {n} stage(s)")

    asyncio.run(_run())


@seed_cli.command("units")
def seed_units() -> None:
    """Seed the unit table. Idempotent; `kerp migrate` runs this too."""
    _seed_units()


DEMO_FORCE = typer.Option(False, "--force", help="seed even if purchases already exist")


@seed_cli.command("demo")
def seed_demo(force: bool = DEMO_FORCE) -> None:
    """Fill a THROWAWAY database with a synthetic household, for demos and screenshots.

    Invented vendors, invented products, example.com people, and coordinates in the
    synthetic grid from SECURITY.md. Never run this against the household's own
    database: it refuses one that already holds purchases unless --force.
    """
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.demo import seed_demo as _seed_demo

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            counts = await _seed_demo(db, force=force)
        await dispose_engine()
        for name, n in counts.items():
            typer.echo(f"  {name}: {n}")

    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


PATH_OPTION = typer.Option(..., "--path", exists=True, file_okay=False, resolve_path=True)


def _import_usda(path: Path) -> None:
    from collections import Counter

    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.usda import UsdaFormatError, import_usda

    async def _run() -> None:
        skipped: Counter[str] = Counter()
        try:
            async with get_sessionmaker()() as db:
                result = await import_usda(db, path, skipped)
        finally:
            await dispose_engine()
        release = result.release_date.isoformat() if result.release_date else "date not in name"
        typer.echo(
            f"imported {result.foods} foods and {result.portions} portions (release {release})"
        )
        for name in result.absent:
            typer.echo(f"  not in the download: {name}")
        for reason, count in sorted(skipped.items()):
            typer.echo(f"  skipped {count}: {reason}")

    try:
        asyncio.run(_run())
    except UsdaFormatError as exc:
        typer.echo(f"error: {exc}. Nothing was imported.", err=True)
        raise typer.Exit(1) from exc


def _import_usda_branded(path: Path) -> None:
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.usda import UsdaFormatError
    from app.services.usda_branded import import_branded

    async def _run() -> None:
        try:
            async with get_sessionmaker()() as db:
                result = await import_branded(db, path)
        finally:
            await dispose_engine()
        typer.echo(f"imported {result.loaded} branded foods by GTIN")
        if result.duplicates:
            typer.echo(f"  {result.duplicates} code(s) listed more than once; the latest row kept")
        if result.invalid:
            typer.echo(f"  {result.invalid} code(s) are not valid GTINs, for example:")
            for code in result.invalid_examples:
                typer.echo(f"    {code}")

    try:
        asyncio.run(_run())
    except UsdaFormatError as exc:
        typer.echo(f"error: {exc}. Nothing was imported.", err=True)
        raise typer.Exit(1) from exc


BRANDED_OPTION = typer.Option(
    False, "--branded", help="the path is a branded-foods download: load the GTIN table (2L)"
)


@import_cli.command("usda")
def import_usda_command(path: Path = PATH_OPTION, branded: bool = BRANDED_OPTION) -> None:
    """Load foods, portions and usage counts from a local, unzipped FoodData Central download.

    With --branded, load branded foods by barcode from the separate branded download.
    """
    if branded:
        _import_usda_branded(path)
    else:
        _import_usda(path)


@import_cli.command("usda-portions")
def import_usda_portions(path: Path = PATH_OPTION) -> None:
    """The earlier name of `kerp import usda`."""
    _import_usda(path)


ingredients_cli = typer.Typer(help="The ingredient vocabulary (1G).", no_args_is_help=True)
cli.add_typer(ingredients_cli, name="ingredients")


@ingredients_cli.command("check")
def ingredients_check() -> None:
    """List what the vocabulary lacks: skipped plurals and missing USDA references."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.spellings import vocabulary_report

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            report = await vocabulary_report(db)
        await dispose_engine()
        skipped = report.skipped_plurals
        typer.echo(f"Plurals skipped because another ingredient has them: {len(skipped)}")
        for s in skipped:
            typer.echo(f"  {s.ingredient}: “{s.plural}” belongs to {s.held_by}")
        typer.echo(f"Ingredients without a USDA reference: {len(report.without_reference)}")
        for name in report.without_reference:
            typer.echo(f"  {name}")
        if not report.usda_loaded:
            typer.echo("USDA data isn't loaded, so references weren't checked against it.")
            return
        absent = report.absent_references
        typer.echo(f"USDA references not in the loaded release: {len(absent)}")
        for name, fdc_id in absent:
            typer.echo(f"  {name}: {fdc_id}")
        standard = report.absent_standard
        typer.echo(f"Standard-list references not in the loaded release: {len(standard)}")
        for key, fdc_id in standard:
            typer.echo(f"  {key}: {fdc_id}")

    asyncio.run(_run())


@ingredients_cli.command("recheck")
def ingredients_recheck() -> None:
    """Offer the standard list again to typed-in ingredients it now matches (link page)."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.ingredient_reconcile import recheck

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            names = await recheck(db)
        await dispose_engine()
        typer.echo(f"Offered on the link page again: {len(names)}")
        for name in names:
            typer.echo(f"  {name}")

    asyncio.run(_run())


@ingredients_cli.command("usda-candidates")
def ingredients_usda_candidates(
    query: str = typer.Argument(..., help="An FDC id (lists its raw or dry siblings) or text."),
) -> None:
    """List USDA foods to choose a reference from, most used by USDA's survey recipes first."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.usda import candidates

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            foods = await candidates(db, query)
        await dispose_engine()
        if not foods:
            typer.echo("No candidates. Is USDA data loaded (kerp import usda)?")
        for food in foods:
            typer.echo(f"{food.fdc_id:>8}  {food.fndds_uses:>4} uses  {food.description}")

    asyncio.run(_run())


osm_cli = typer.Typer(help="OpenStreetMap links (needs ENABLE_OVERPASS).", no_args_is_help=True)
cli.add_typer(osm_cli, name="osm")


@osm_cli.command("refresh")
def osm_refresh(
    all_linked: bool = typer.Option(
        False, "--all-linked", help="Refresh every active location linked to OpenStreetMap."
    ),
) -> None:
    """Re-read linked locations from OpenStreetMap. A field a person edited is kept."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.core.errors import ApiError
    from app.services import geo, osm

    if not all_linked:
        typer.echo("Name what to refresh: --all-linked.", err=True)
        raise typer.Exit(2)
    try:
        osm.ensure_enabled()
    except ApiError as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(2) from exc

    async def _run() -> int:
        failed = 0
        async with get_sessionmaker()() as db:
            targets = await geo.linked_location_ids(db)
            for location_id, name in targets:
                try:
                    await geo.refresh_osm(db, location_id)
                    typer.echo(f"  refreshed {name}")
                except ApiError as exc:
                    await db.rollback()
                    failed += 1
                    typer.echo(f"  {name}: {exc.message}", err=True)
            typer.echo(f"{len(targets) - failed} of {len(targets)} linked location(s) refreshed")
        await dispose_engine()
        return failed

    if asyncio.run(_run()):
        raise typer.Exit(1)


export_cli = typer.Typer(help="Export data as files.", no_args_is_help=True)
cli.add_typer(export_cli, name="export")
EXPORT_OUT = typer.Option(..., "--out", help="File to write")


@export_cli.command("vendors")
def export_vendors(
    fmt: str = typer.Option("yaml", "--format", help="yaml or json"),
    mode: str = typer.Option("public", "--mode", help="public or household"),
    out: Path = EXPORT_OUT,
) -> None:
    """Write the vendor list as a kitchen-erp-vendors file (/1, or /2 when it holds product facts).

    Public mode holds only what may be contributed; household mode holds
    everything, including store codes and home bases: keep it private.
    """
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services import vendor_exchange

    if fmt not in ("yaml", "json") or mode not in ("public", "household"):
        typer.echo("--format is yaml or json; --mode is public or household.", err=True)
        raise typer.Exit(2)

    async def _run() -> tuple[int, int]:
        async with get_sessionmaker()() as db:
            file = await vendor_exchange.build(db, mode)  # type: ignore[arg-type]
        await dispose_engine()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(vendor_exchange.render(file, fmt))
        return len(file.vendors), sum(len(v.locations) for v in file.vendors)

    vendors, locations = asyncio.run(_run())
    typer.echo(f"wrote {out}: {vendors} vendor(s), {locations} location(s), {mode} mode")


VENDOR_FILE = typer.Option(..., "--from", exists=True, dir_okay=False, resolve_path=True)


@import_cli.command("vendors")
def import_vendors(
    src: Path = VENDOR_FILE,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change; write nothing."
    ),
) -> None:
    """Import a kitchen-erp-vendors/1 or /2 file (YAML or JSON). A field someone edited is kept."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.core.errors import ApiError
    from app.services import vendor_import

    fmt = "json" if src.suffix.lower() == ".json" else "yaml"

    async def _run():
        async with get_sessionmaker()() as db:
            report = await vendor_import.run(
                db, src.read_bytes(), fmt=fmt, dry_run=dry_run, filename=src.name
            )
        await dispose_engine()
        return report

    try:
        report = asyncio.run(_run())
    except ApiError as exc:
        typer.echo(f"{exc.message}", err=True)
        raise typer.Exit(2) from exc
    for item in report.items:
        if item.outcome in ("conflict", "unmatched"):
            what = item.reason or "; ".join(
                f"your {c.field} is kept (the file says {c.file!r})" for c in item.conflicts
            )
            typer.echo(f"  needs you: {item.key}: {what}")
    for name in report.unresolved_home_bases:
        typer.echo(f"  no home base called {name!r}: those locations use the nearest one")
    c = report.counts
    verb = "would change" if dry_run else "changed"
    typer.echo(
        f"{verb}: {c.created} created, {c.updated} updated, {c.unchanged} unchanged, "
        f"{c.conflicts} conflicts, {c.unmatched} unmatched"
    )


@cli.command("recompute-norms")
def recompute_norms() -> None:
    """Truncate and rebuild price_norm from observations and current bridges."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.pricebook import recompute_all

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            n = await recompute_all(db)
        await dispose_engine()
        typer.echo(f"recomputed {n} normalizations")

    asyncio.run(_run())


OUT_OPTION = typer.Option(..., "--out", file_okay=False, resolve_path=True)
FROM_OPTION = typer.Option(..., "--from", exists=True, file_okay=False, resolve_path=True)
FORCE_OPTION = typer.Option(False, "--force", help="overwrite a non-empty database")


BACKUP_FORCE = typer.Option(False, "--force", help="replace a backup already in the directory")


@cli.command()
def backup(out: Path = OUT_OPTION, force: bool = BACKUP_FORCE) -> None:
    """Write a database dump, the receipt images, and a manifest to a directory."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.core.errors import ApiError
    from app.services.backup import backup as _backup

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            try:
                manifest = await _backup(db, out, force=force)
            except ApiError as exc:
                typer.echo(f"error: {exc.message}", err=True)
                raise typer.Exit(code=1) from exc
        await dispose_engine()
        typer.echo(
            f"backup written to {out}: {manifest['counts']}, "
            f"{len(manifest['receipts'])} receipt image(s) and "
            f"{len(manifest['media'])} photo file(s)"
        )

    asyncio.run(_run())


@cli.command()
def restore(src: Path = FROM_OPTION, force: bool = FORCE_OPTION) -> None:
    """Restore a backup directory into this deployment."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.core.errors import ApiError
    from app.services.backup import restore as _restore

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            try:
                result = await _restore(db, src, force=force)
            except ApiError as exc:
                typer.echo(f"error: {exc.message}", err=True)
                raise typer.Exit(code=1) from exc
        await dispose_engine()
        typer.echo(
            f"restored {result['counts']}, {result['restored_receipts']} receipt image(s) "
            f"and {result['restored_media']} photo file(s)"
        )
        for path in result["media_mismatches"]:
            typer.echo(f"hash mismatch: media/{path}", err=True)
        if result["media_mismatches"]:
            raise typer.Exit(code=1)

    asyncio.run(_run())


images_cli = typer.Typer(help="Product photos (1I).", no_args_is_help=True)
cli.add_typer(images_cli, name="images")


@images_cli.command("rebuild")
def images_rebuild() -> None:
    """Make every missing derivative for the current pipeline version."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services import media, product_photos

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            count = await product_photos.rebuild_derivatives(db)
        await dispose_engine()
        typer.echo(f"{count} derivative file(s) present for pipeline {media.pipeline_version()}")

    asyncio.run(_run())


@images_cli.command("reselect")
def images_reselect() -> None:
    """Choose every product's main photo again by the current rule."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services import product_photos

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            changed = await product_photos.reselect_all(db)
        await dispose_engine()
        typer.echo(f"{changed} product(s) have a different main photo")

    asyncio.run(_run())


IMPORT_FILE = typer.Option(..., "--from", exists=True, dir_okay=False, resolve_path=True)
AS_USER = typer.Option(..., "--as", help="email of the user recorded as entering the purchases")
FALLBACK_LOCATION = typer.Option(
    None, "--location", help="location id used when no store code matches"
)


@import_cli.command("purchases")
def import_purchases(
    src: Path = IMPORT_FILE, user_email: str = AS_USER, location_id: str | None = FALLBACK_LOCATION
) -> None:
    """Import a retailer purchase export (kitchen-erp-purchase-export/1 JSON)."""
    import uuid

    from sqlalchemy import select

    from app.core.db import dispose_engine, get_sessionmaker
    from app.core.errors import ApiError
    from app.models import AppUser
    from app.services.importer import import_export, load_export

    try:
        export = load_export(src)
    except ApiError as exc:
        typer.echo(f"error: {exc.message}", err=True)
        raise typer.Exit(code=1) from exc

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            user = (
                await db.execute(select(AppUser).where(AppUser.email == user_email.lower()))
            ).scalar_one_or_none()
            if user is None:
                typer.echo(f"error: no user {user_email}", err=True)
                raise typer.Exit(code=1)
            result = await import_export(
                db,
                user,
                export,
                fallback_location_id=uuid.UUID(location_id) if location_id else None,
            )
        await dispose_engine()
        typer.echo(
            f"imported {result['created']} purchase(s), skipped {result['skipped']} already "
            f"present, {result['unlocated']} without a matching location"
        )
        if result["unlocated"]:
            # Otherwise a first import reports three numbers, imports nothing, and
            # leaves no clue what a "matching location" is.
            typer.echo(
                f"{result['unlocated']} transaction(s) matched no location of "
                f"{export.retailer!r}. Give that vendor one location (a vendor with a "
                "single location is used automatically), pass --location <location id>, "
                "or add the store code to a location (vendor page, Edit, Store codes on receipts).",
                err=True,
            )

    asyncio.run(_run())


BENCH_MODELS = typer.Option(
    None,
    "--models",
    help="Comma-separated vision models for arm (c). Alone, it runs only arm (c), "
    "against the stored rows.",
)
BENCH_ARMS = typer.Option(
    None,
    "--arms",
    help="Comma-separated arms to run (a, b, c; default all, or c with --models), "
    "against the stored rows for the others.",
)
BENCH_RESUME = typer.Option(None, "--resume", help="Continue a stopped run by its RUN_ID.")
BENCH_EXPECTED = typer.Option(
    None,
    "--expected",
    exists=True,
    dir_okay=False,
    resolve_path=True,
    help="CSV of document_id,total,item_line_count for receipts never committed.",
)
BENCH_FORCE = typer.Option(False, "--force", help="Run even though a worker is connected.")
BENCH_OUT = typer.Option(
    None, "--out", file_okay=False, help="Where runs go (default: beside the receipts store)."
)
BENCH_OCR_MODEL = typer.Option("glm-ocr", "--ocr-model", help="Arm (b)'s transcription model.")
BENCH_TEXT_MODEL = typer.Option(
    None,
    "--text-model",
    help="Arms (a) and (b)'s text model, or several, comma-separated (default: LLM_MODEL).",
)


@cli.command("reading-benchmark")
def reading_benchmark(
    models: str | None = BENCH_MODELS,
    arms: str | None = BENCH_ARMS,
    resume: str | None = BENCH_RESUME,
    expected: Path | None = BENCH_EXPECTED,
    force: bool = BENCH_FORCE,
    out: Path | None = BENCH_OUT,
    ocr_model: str = BENCH_OCR_MODEL,
    text_model: str | None = BENCH_TEXT_MODEL,
) -> None:
    """Measure receipt readers on committed receipts (spec 04, 2J). Writes no app data.

    Stop the worker first (docker compose stop worker), run detached, and read
    summary.txt when it is done. Progress is checkpointed per reading.
    """
    from app.core.config import get_settings
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services import reading_benchmark as bench

    settings = get_settings()
    configure_logging(settings.log_level)

    def _split(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

    wanted = _split(arms) if arms else (["c"] if models else list(bench.ARMS))
    if unknown := sorted(set(wanted) - set(bench.ARMS)):
        typer.echo(f"error: unknown arm(s) {', '.join(unknown)}; use a, b and c.", err=True)
        raise typer.Exit(code=2)

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            try:
                selection = await bench.select_receipts(db, expected)
                running = await bench.worker_running(db)
            finally:
                await db.rollback()
        await dispose_engine()
        if running and not force:
            typer.echo(
                "error: a worker is connected. It would swap models during the run. "
                "Stop it (docker compose stop worker) or pass --force.",
                err=True,
            )
            raise typer.Exit(code=1)
        if not selection.receipts:
            typer.echo(
                f"error: no eligible receipts (excluded: {selection.excluded}). Commit some "
                "receipts, or pass --expected with rows for uploaded ones.",
                err=True,
            )
            raise typer.Exit(code=1)
        configs = bench.default_configs(
            text_models=_split(text_model) if text_model else [settings.llm_model],
            ocr_model=ocr_model,
            vision_models=_split(models) if models else bench.DEFAULT_VISION_MODELS,
            arms=wanted,
            think=settings.llm_think,
        )
        options = bench.RunOptions(
            out_dir=out or Path(settings.receipts_path).parent / "benchmarks",
            configs=configs,
            expected_csv=expected,
            resume=resume,
            compare_with_stored=bool(models) or set(wanted) != set(bench.ARMS),
        )
        result = await bench.run(selection, options, echo=typer.echo)
        typer.echo("")
        typer.echo(result.summary)

    asyncio.run(_run())


OPENAPI_OUT = typer.Option(
    Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json",
    "--out",
    dir_okay=False,
    resolve_path=True,
)


@cli.command("export-openapi")
def export_openapi(out: Path = OPENAPI_OUT) -> None:
    """Write the running application's OpenAPI document (the capture contract) to disk."""
    import json

    from app.main import app
    from app.schemas.products_interchange import contract_schema

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    typer.echo(f"wrote {out}")
    # The products helper's contract (2N) sits beside it; the helper checks against it.
    helper = out.parent / "kitchen-erp-products-1.schema.json"
    helper.write_text(json.dumps(contract_schema(), indent=2, sort_keys=True) + "\n")
    typer.echo(f"wrote {helper}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
