"""`kerp receipts`: stored receipt files and their readings."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import typer

receipts_cli = typer.Typer(help="Stored receipt files.", no_args_is_help=True)


REREAD_AS = typer.Option(..., "--as", help="email of the user recorded as asking for the re-reads")


@receipts_cli.command("reread")
def reread_receipts(
    user_email: str = REREAD_AS,
    mode: str = typer.Option(
        "check",
        "--mode",
        help="check: run today's line passes over each reader's kept answer (no model). "
        "read: ask the reader again, through the worker.",
    ),
    since: str | None = typer.Option(
        None, "--since", help="Only receipts bought on or after this day (YYYY-MM-DD)."
    ),
) -> None:
    """Make a re-read proposal for every receipt purchase (04, 2S)."""
    from datetime import date, time

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.db import dispose_engine, get_sessionmaker
    from app.models import AppUser
    from app.services import reread

    if mode not in ("check", "read"):
        typer.echo("error: --mode is check or read", err=True)
        raise typer.Exit(code=2)
    start = None
    if since is not None:
        try:
            day = date.fromisoformat(since)
        except ValueError:
            typer.echo("error: --since is a day, YYYY-MM-DD", err=True)
            raise typer.Exit(code=2) from None
        zone = ZoneInfo(get_settings().household_timezone)
        start = datetime.combine(day, time(), tzinfo=zone)

    async def _run() -> dict[str, int]:
        try:
            async with get_sessionmaker()() as db:
                user = (
                    await db.execute(select(AppUser).where(AppUser.email == user_email.lower()))
                ).scalar_one_or_none()
                if user is None:
                    typer.echo(f"error: no user {user_email}", err=True)
                    raise typer.Exit(code=1)
                return await reread.request_many(db, user, mode, since=start)
        finally:
            await dispose_engine()

    counts = asyncio.run(_run())
    if mode == "check":
        typer.echo(f"{counts['ready']} receipt(s) would change: review them from the inbox.")
        typer.echo(f"{counts['no_changes']} already read as today's passes read them.")
        if counts["not_stored"]:
            typer.echo(
                f"{counts['not_stored']} skipped: read before the reader's answer was kept. "
                "Use --mode read for those."
            )
    else:
        typer.echo(f"{counts['pending']} receipt(s) queued to be read again by the worker.")
    if counts["already_open"]:
        typer.echo(f"{counts['already_open']} skipped: a re-read is already open.")


@receipts_cli.command("strip-metadata")
def strip_receipt_metadata(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Count what would change; change nothing."
    ),
) -> None:
    """Remove GPS and other metadata from receipt photos stored before upgrading (#221)."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services import receipt_strip

    async def _run() -> receipt_strip.Report:
        try:
            async with get_sessionmaker()() as db:
                return await receipt_strip.run(db, dry_run=dry_run)
        finally:
            await dispose_engine()

    report = asyncio.run(_run())
    if report.missing:
        typer.echo(
            f"error: {len(report.missing)} receipt files are missing, so nothing was changed. "
            "Restore them from a backup first. Receipt ids:",
            err=True,
        )
        for doc_id in report.missing:
            typer.echo(f"  {doc_id}", err=True)
        raise typer.Exit(code=1)
    labels = {
        "stripped": "would be stripped" if dry_run else "stripped",
        "reencoded": "would be re-encoded as JPEG" if dry_run else "re-encoded as JPEG",
        "unchanged": "had no metadata",
        "pdf": "are PDFs, left as they are",
        "removed": "were removed, with no file to strip",
        "in_use": "are being read; run this again when the worker is idle",
        "collision": "would duplicate another receipt's file; left as they are",
        "unreadable": "can't be read and still carry metadata; left as they are",
    }
    for outcome, label in labels.items():
        if report.counts[outcome]:
            typer.echo(f"  {report.counts[outcome]:>5}  {label}")
    if not any(report.counts.values()):
        typer.echo("No receipts are stored.")
    if dry_run:
        typer.echo("Nothing was changed. Run without --dry-run to apply.")


@receipts_cli.command("row-boxes")
def find_row_boxes(
    limit: int = typer.Option(
        0, "--limit", help="Stop after this many receipts (0: every one that needs them)."
    ),
) -> None:
    """Find where each printed row sits on the receipt photo, for review's outlines (issue 380).

    For every read receipt whose latest text has no boxes yet, newest first. The
    worker does the same when idle; this catches up at once. Needs Tesseract.
    """
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services import row_boxes

    if not row_boxes.tesseract_installed():
        typer.echo("error: tesseract is not installed here; run this in the worker", err=True)
        raise typer.Exit(code=1)

    async def _run() -> tuple[int, int]:
        found = failed = 0
        skip: set = set()
        try:
            async with get_sessionmaker()() as db:
                while not limit or found + failed < limit:
                    pending = await row_boxes.jobs_needing_boxes(db, skip=skip)
                    if not pending:
                        break
                    if await row_boxes.find_for_job(db, pending[0]) is None:
                        skip.add(pending[0])
                        failed += 1
                    else:
                        found += 1
        finally:
            await dispose_engine()
        return found, failed

    found, failed = asyncio.run(_run())
    typer.echo(f"{found} receipt(s) given row boxes.")
    if failed:
        typer.echo(f"{failed} receipt(s) Tesseract could not read; review shows them without.")
