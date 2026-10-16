"""`kerp osm`: OpenStreetMap links on locations (needs ENABLE_OVERPASS)."""

from __future__ import annotations

import asyncio

import typer

osm_cli = typer.Typer(help="OpenStreetMap links (needs ENABLE_OVERPASS).", no_args_is_help=True)


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
