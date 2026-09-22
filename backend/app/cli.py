"""The `kerp` command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from alembic.config import Config

from alembic import command
from app.core.logging import configure_logging

cli = typer.Typer(help="Kitchen ERP administration.", no_args_is_help=True)
seed_cli = typer.Typer(help="Seed reference data.", no_args_is_help=True)
cli.add_typer(seed_cli, name="seed")
import_cli = typer.Typer(help="Import reference data.", no_args_is_help=True)
cli.add_typer(import_cli, name="import")

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


@cli.command()
def migrate(revision: str = typer.Argument("head")) -> None:
    """Apply migrations as the owner role, then seed reference units (idempotent)."""
    command.upgrade(alembic_config(), revision)
    if revision == "head":
        _seed_units()


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
    command.downgrade(alembic_config(), revision)


@cli.command("create-admin")
def create_admin(
    email: str = typer.Option(..., prompt=True),
    display_name: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Create the first administrator (or another one)."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services import identity

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            user = await identity.create_user(
                db, email=email, display_name=display_name, password=password, role="admin"
            )
            typer.echo(f"created admin {user.email} ({user.id})")
        await dispose_engine()

    asyncio.run(_run())


@cli.command()
def worker() -> None:
    """Run the ingest worker."""
    from app.core.config import get_settings
    from app.worker import run

    configure_logging(get_settings().log_level)
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


@import_cli.command("usda-portions")
def import_usda_portions(path: Path = PATH_OPTION) -> None:
    """Load food portions from a local USDA FoodData Central CSV download."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.usda import import_portions

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            n = await import_portions(db, path)
        await dispose_engine()
        typer.echo(f"imported {n} portions")

    asyncio.run(_run())


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


@cli.command()
def backup(out: Path = OUT_OPTION) -> None:
    """Write a database dump, the receipt images, and a manifest to a directory."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.backup import backup as _backup

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            manifest = await _backup(db, out)
        await dispose_engine()
        typer.echo(
            f"backup written to {out}: {manifest['counts']} and "
            f"{len(manifest['receipts'])} receipt image(s)"
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
            f"restored {result['counts']} and {result['restored_receipts']} receipt image(s)"
        )

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
    from app.models import AppUser
    from app.services.importer import import_export, load_export

    async def _run() -> None:
        export = load_export(src)
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

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    typer.echo(f"wrote {out}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
