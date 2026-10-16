"""`kerp naming`: the naming pass's learned words (04, 2I; #388)."""

from __future__ import annotations

import asyncio

import typer

naming_cli = typer.Typer(help="Naming new products from receipt lines.", no_args_is_help=True)


@naming_cli.command("learn-words")
def learn_words() -> None:
    """Learn what printed words stand for from every line already identified."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.line_words import backfill

    async def _run() -> None:
        async with get_sessionmaker()() as db:
            added = await backfill(db)
            await db.commit()
        await dispose_engine()
        typer.echo(f"Learned words added: {added}")

    asyncio.run(_run())
