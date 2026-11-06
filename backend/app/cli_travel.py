"""`kerp travel-legs`: drive-time legs between places (17, 4B)."""

from __future__ import annotations

import asyncio

import typer

travel_legs_cli = typer.Typer(help="Drive-time legs between places (17, 4B).", no_args_is_help=True)


@travel_legs_cli.command("recompute")
def travel_legs_recompute(
    all_legs: bool = typer.Option(
        False, "--all", help="Drop the engine's legs too, not only the estimates."
    ),
) -> None:
    """Drop estimated legs and compute them again from the routing engine, or
    estimate them again when it is off (17, 4B)."""
    from app.core.config import get_settings
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.travel import recompute_legs

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            counts = await recompute_legs(db, get_settings(), only_estimates=not all_legs)
        await dispose_engine()
        typer.echo(
            f"dropped {counts.dropped} legs; {counts.osrm} from the engine, "
            f"{counts.estimated} estimated"
        )

    asyncio.run(_run())
