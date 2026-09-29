"""Alembic environment. Migrations run as the owner role over an async engine."""

from __future__ import annotations

import asyncio

import click
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import get_settings
from app.models import Base

config = context.config
target_metadata = Base.metadata


def _report(ctx, step, heads, run_args) -> None:
    """One line per migration as it runs, so `kerp migrate` shows its progress.

    Every step runs in one transaction: "ran" means done inside it, and the
    command says when the whole set commits. The steps are also kept on the
    config, for that summary.
    """
    if step.is_stamp:
        return
    script = step.up_revision
    verb = "ran" if step.is_upgrade else "reverted"
    doc = f" · {script.doc}" if script is not None and script.doc else ""
    click.echo(f"  {verb} {step.up_revision_id}{doc}")
    config.attributes.setdefault("steps", []).append(step.up_revision_id)


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().migration_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, on_version_apply=_report
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
