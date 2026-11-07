"""`kerp flyers`: weekly flyer sources (18, 2U).

``import`` reads a household's seed file, YAML like::

    sources:
      - location: larkspur-market/main-street   # the vendor location's key
        mechanism: itemized
        adapter: example_itemized
        store_ref: {store: "0042"}
        publish_weekday: 3                      # ISO weekday, 1 = Monday
        enabled: true

and creates or updates one source per location. The file names real stores, so it
lives under ``data/`` and is never committed (18, Privacy).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
import yaml
from pydantic import ValidationError

flyers_cli = typer.Typer(help="Weekly flyer sources (18, 2U).", no_args_is_help=True)


SEED_ARGUMENT = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True)


@flyers_cli.command("import")
def import_sources(path: Path = SEED_ARGUMENT) -> None:
    """Create or update the flyer sources a seed file lists; unmatched keys are reported."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.flyers import SeedFile, import_sources

    try:
        seed = SeedFile.model_validate(yaml.safe_load(path.read_text()))
    except (yaml.YAMLError, ValidationError) as exc:
        typer.echo(f"The seed file isn't valid: {exc}", err=True)
        raise typer.Exit(1) from None

    async def _run():
        async with get_sessionmaker()() as db:
            out = await import_sources(db, seed)
        await dispose_engine()
        return out

    out = asyncio.run(_run())
    typer.echo(f"{out.created} created, {out.updated} updated, {out.unchanged} unchanged.")
    for key in out.unmatched:
        typer.echo(f"No vendor location with key {key}: skipped.")
    if out.unmatched:
        raise typer.Exit(1)
