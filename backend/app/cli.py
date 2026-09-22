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


@seed_cli.command("units")
def seed_units() -> None:
    """Seed the unit table. Idempotent; `kerp migrate` runs this too."""
    _seed_units()


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


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
