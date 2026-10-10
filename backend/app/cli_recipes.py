"""`kerp recipes`: the recipe vocabulary on the host (07, 1G amendments; package 9).

Three commands over a directory of ``.cook`` files: ``vocabulary`` reports every
distinct ingredient name bucketed against the catalog, ``lint`` lists the names
that are neither a name nor a spelling (``--strict`` fails on them), and
``conform`` rewrites drift names to their target, in place, with every other
byte unchanged. The catalog's names and spellings come from the database; the
files are read from the path given, or, for the two read-only commands, from the
configured recipes mount. ``conform`` always needs an explicit ``--path`` and
never commits: the running application never writes to the mount, and `git`
is a person's job.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

recipes_cli = typer.Typer(
    help="The recipe vocabulary: report, lint and conform .cook files on the host.",
    no_args_is_help=True,
)

PATH_OPTION = typer.Option(
    None,
    "--path",
    help="Directory of .cook files. Default: the configured recipes mount.",
    exists=True,
    file_okay=False,
    resolve_path=True,
)
REQUIRED_PATH_OPTION = typer.Option(
    ...,
    "--path",
    help="Directory of .cook files to rewrite. Required: conform never defaults to the mount.",
    exists=True,
    file_okay=False,
    resolve_path=True,
)
OUT_OPTION = typer.Option(
    None,
    "--out",
    help="Write the report here (.json, or .csv by suffix). Default: under data/vocabulary/.",
    dir_okay=False,
    resolve_path=True,
)
STRICT_OPTION = typer.Option(
    False, "--strict", help="Exit 1 when any name is drift or unknown, or a file does not parse."
)
APPLY_OPTION = typer.Option(
    False, "--apply", help="Write the rewrites. Without it, print them as a unified diff."
)


def _context():
    """The catalog's names, spellings, negligible and ignored names, loaded once."""
    from app.core.db import dispose_engine, get_sessionmaker
    from app.services.recipe_resolution import load_context

    async def _run():
        async with get_sessionmaker()() as db:
            ctx = await load_context(db)
        await dispose_engine()
        return ctx

    return asyncio.run(_run())


def _root(path: Path | None) -> Path:
    if path is not None:
        return path
    from app.core.config import get_settings

    root = Path(get_settings().recipes_path)
    if not root.is_dir():
        typer.echo(f"error: no recipes directory at {root}; pass --path", err=True)
        raise typer.Exit(code=2)
    return root


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


@recipes_cli.command("vocabulary")
def vocabulary(path: Path | None = PATH_OPTION, out: Path | None = OUT_OPTION) -> None:
    """Report every distinct ingredient name bucketed against the catalog; write it to a file."""
    from app.services import recipe_vocabulary as vocab

    root = _root(path)
    ctx = _context()
    report = vocab.build_report(ctx, root, vocab.read_files(root))
    typer.echo(vocab.table(report))
    written = report.write(out or vocab.default_report_path())
    typer.echo(f"Report written to {written}")


@recipes_cli.command("lint")
def lint(path: Path | None = PATH_OPTION, strict: bool = STRICT_OPTION) -> None:
    """List names that are neither a name nor a spelling, with a suggestion where there is one."""
    from app.services import recipe_vocabulary as vocab

    root = _root(path)
    ctx = _context()
    report = vocab.build_report(ctx, root, vocab.read_files(root))
    for err in report.parse_errors:
        typer.echo(f"parse error  {err['path']}: {err['error']}", err=True)
    flagged = report.flagged()
    for n in flagged:
        typer.echo(
            f"{n.bucket:<8} {vocab.describe(n)}  "
            f"[{_plural(n.count, 'line')} in {_plural(len(n.files), 'file')}]"
        )
    drift = sum(1 for n in flagged if n.bucket == "drift")
    unknown = len(flagged) - drift
    if not flagged and not report.parse_errors:
        typer.echo(f"Every name in {_plural(report.files, 'file')} is a name or a spelling.")
        return
    typer.echo(
        f"{_plural(report.files, 'file')}: {drift} drift, {unknown} unknown, "
        f"{_plural(len(report.parse_errors), 'parse error')}."
    )
    if strict:
        raise typer.Exit(code=1)


@recipes_cli.command("conform")
def conform(path: Path = REQUIRED_PATH_OPTION, apply: bool = APPLY_OPTION) -> None:
    """Rewrite drift names to their target in place. Prints a diff unless --apply; never commits."""
    from app.services import recipe_vocabulary as vocab

    ctx = _context()
    plan = vocab.plan_conform(ctx, path, vocab.read_files(path))
    for err in plan.parse_errors:
        typer.echo(f"parse error  {err['path']}: {err['error']}  (skipped)", err=True)
    for err in plan.unsafe:
        typer.echo(f"skipped  {err['path']}: {err['error']}", err=True)
    if not plan.changes:
        typer.echo("No drift: nothing to rewrite.")
        return
    tokens = plan.tokens
    if not apply:
        typer.echo(vocab.unified_diff(plan), nl=False)
        typer.echo(
            f"{_plural(tokens, 'rewrite')} in {_plural(len(plan.changes), 'file')} would be made. "
            "Run with --apply to write them."
        )
        return
    written = vocab.apply_plan(plan)
    typer.echo(f"Rewrote {_plural(tokens, 'token')} in {_plural(len(written), 'file')}:")
    for rel in written:
        typer.echo(f"  {rel}")
    typer.echo("Nothing was committed: read `git diff` in the repository and commit it yourself.")
