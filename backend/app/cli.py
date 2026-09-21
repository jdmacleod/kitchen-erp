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

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


@cli.command()
def migrate(revision: str = typer.Argument("head")) -> None:
    """Apply migrations as the owner role."""
    command.upgrade(alembic_config(), revision)


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
    """Seed the unit table (Phase 1B)."""
    typer.echo("units seeding arrives with Phase 1B", err=True)
    raise typer.Exit(code=2)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
