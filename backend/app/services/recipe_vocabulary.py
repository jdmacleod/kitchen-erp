"""The recipe vocabulary on the host: report, lint and conform (07, 1G amendments).

Three `kerp recipes` commands share this module. Each walks the ``.cook`` files
under a directory, parses them with the Cooklang parser, and sorts every
distinct ingredient name into a bucket against the catalog's names and
spellings, loaded once as a :class:`~app.services.recipe_resolution.Context`:

- ``conformant``: an exact name or spelling (the lookup's first step);
- ``inflection``: resolves through 1G's ``singulars`` ("eggs" for egg);
- ``drift``: no exact hit, but a confident different target: an exact
  standard-list entry (``standard_match.match_entry``), or the name without its
  prep words found exactly ("minced garlic" → garlic, note "minced");
- ``unknown``: nothing the catalog or the standard list knows;
- ``negligible`` and ``ignored``: names the application never counts
  (``RECIPES_NEGLIGIBLE_NAMES``, ``recipe_name_ignore``). Lint leaves them alone.

The buckets are the first steps of 3C's cascade, read the same way: only an
exact name, spelling or inflection is conformant (VC3); drift is what
``conform`` may rewrite, and it is exactly what the queue would propose from
its standard and prep tiers. Trigram similarity never counts as drift.

``conform`` is a host command, never an application feature: the running
application never writes to the recipes mount. It rewrites only the ingredient
token spans the parser recorded, adds ``{}`` when a single-word name becomes a
multi-word one, moves stripped prep words into the note, writes the file back
with every other byte unchanged, and never commits. Reports go under
``data/``, never into the mount and never into the repository.
"""

from __future__ import annotations

import csv
import difflib
import json
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.catalog.names import normalize_name, singulars
from app.catalog.standard_match import match_entry
from app.core.config import Settings, get_settings
from app.recipes.cooklang import IngredientRef, ParseError, Recipe, parse
from app.recipes.prep import strip_prep
from app.services.recipe_resolution import Context, _exact, lookup

Bucket = Literal["conformant", "inflection", "drift", "unknown", "negligible", "ignored"]
BUCKETS: tuple[Bucket, ...] = (
    "conformant",
    "inflection",
    "drift",
    "unknown",
    "negligible",
    "ignored",
)
# What lint reports and what --strict fails on.
FLAGGED: frozenset[str] = frozenset({"drift", "unknown"})
TargetKind = Literal["ingredient", "standard"]

_BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"
_LINE_BREAK = re.compile(r"(\r\n|\r|\n)")
# Characters the parser accepts inside a single-word name between two letters.
_JOINERS = "-_'’"


# --- classification ---------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """Which bucket a normalized name falls in, and where it points when it drifts."""

    bucket: Bucket
    target: str | None = None  # the ingredient's or standard entry's name
    target_kind: TargetKind | None = None
    note: str | None = None  # stripped prep words, for drift through stripping
    hint: str | None = None  # for unknown: what nearly fit


def _standard_target(ctx: Context, name_norm: str) -> tuple[str, TargetKind] | None:
    """What the standard tier would offer: (name, kind), or None.

    The entry's catalog ingredient when there is an active one, else the entry
    itself to create from. An inactive ingredient holding the entry offers
    nothing, as in ``recipe_resolution._standard_tier``.
    """
    entry = match_entry(name_norm)
    if entry is None:
        return None
    existing = ctx.index.by_slug.get(entry.key)
    if existing is None:
        existing = next(iter(ctx.index.by_name.get(entry.name.lower(), [])), None)
    if existing is not None:
        return (existing.name, "ingredient") if existing.active else None
    return entry.name, "standard"


def classify(ctx: Context, name_norm: str) -> Classification:
    """The bucket of one normalized name. Pure given the context."""
    if name_norm in ctx.negligible:
        return Classification("negligible")
    if name_norm in ctx.ignored:
        return Classification("ignored")
    hit = _exact(ctx.index, name_norm)
    if hit is not None:
        return Classification("conformant", hit.name, "ingredient")
    for single in singulars(name_norm):
        hit = _exact(ctx.index, single)
        if hit is not None:
            return Classification("inflection", hit.name, "ingredient")
    # Drift: a different target the cascade's first proposing tiers are sure of.
    standard = _standard_target(ctx, name_norm)
    if standard is not None and normalize_name(standard[0]) != name_norm:
        return Classification("drift", standard[0], standard[1])
    stripped = strip_prep(name_norm)
    if stripped is not None:
        remainder = stripped.remainder
        if remainder not in ctx.negligible and remainder not in ctx.ignored:
            hit = lookup(ctx, remainder)
            if hit is not None:
                return Classification("drift", hit.name, "ingredient", note=stripped.note)
            of_remainder = _standard_target(ctx, remainder)
            if of_remainder is not None:
                return Classification("drift", of_remainder[0], of_remainder[1], note=stripped.note)
    hint = None
    if standard is not None and standard[1] == "standard":
        hint = f"the standard list has “{standard[0]}”, which the catalog does not have yet"
    return Classification("unknown", hint=hint)


# --- the files ----------------------------------------------------------------------------


@dataclass(frozen=True)
class CookFile:
    """One ``.cook`` file as read: its text as on disk and its parse."""

    rel: str  # posix path relative to the root
    path: Path
    text: str
    parsed: Recipe | ParseError


def list_cook_files(root: Path) -> list[Path]:
    """Every ``*.cook`` under ``root`` in a stable order, never descending into dot directories."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith(".cook") and not name.startswith("."):
                files.append(Path(dirpath) / name)
    return files


def read_files(root: Path) -> list[CookFile]:
    out: list[CookFile] = []
    for path in list_cook_files(root):
        # newline="" keeps the file's own line breaks, so a rewrite can keep them too.
        with path.open(encoding="utf-8", newline="") as fh:
            text = fh.read()
        out.append(CookFile(path.relative_to(root).as_posix(), path, text, parse(text)))
    return out


# --- the report ---------------------------------------------------------------------------


@dataclass
class NameReport:
    name_norm: str
    bucket: Bucket
    count: int
    raw_names: list[str]  # distinct spellings as written, most used first
    files: list[str]
    target: str | None = None
    target_kind: str | None = None
    note: str | None = None
    hint: str | None = None


@dataclass
class VocabularyReport:
    root: str
    generated_at: str
    files: int
    parse_errors: list[dict[str, str]]
    names: list[NameReport] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counter = Counter(n.bucket for n in self.names)
        return {bucket: counter.get(bucket, 0) for bucket in BUCKETS}

    def flagged(self) -> list[NameReport]:
        return [n for n in self.names if n.bucket in FLAGGED]

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "files": self.files,
            "counts": self.counts,
            "parse_errors": self.parse_errors,
            "names": [asdict(n) for n in self.names],
        }

    def write(self, out: Path) -> Path:
        """Write the report as JSON, or as CSV when the path ends in ``.csv``."""
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".csv":
            with out.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(
                    ["name_norm", "bucket", "count", "target", "note", "hint", "raw_names", "files"]
                )
                for n in self.names:
                    w.writerow(
                        [
                            n.name_norm,
                            n.bucket,
                            n.count,
                            n.target or "",
                            n.note or "",
                            n.hint or "",
                            "; ".join(n.raw_names),
                            "; ".join(n.files),
                        ]
                    )
        else:
            out.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", "utf-8")
        return out


_BUCKET_RANK = {bucket: i for i, bucket in enumerate(BUCKETS)}


def build_report(ctx: Context, root: Path, files: Iterable[CookFile]) -> VocabularyReport:
    """Every distinct normalized name across the files, bucketed; most used first in a bucket."""
    files = list(files)
    counts: Counter[str] = Counter()
    raw: dict[str, Counter[str]] = {}
    where: dict[str, set[str]] = {}
    errors: list[dict[str, str]] = []
    for f in files:
        if isinstance(f.parsed, ParseError):
            errors.append({"path": f.rel, "error": str(f.parsed)})
            continue
        for ref in f.parsed.ingredients:
            key = normalize_name(ref.raw_name)
            if not key:
                continue
            counts[key] += 1
            raw.setdefault(key, Counter())[ref.raw_name] += 1
            where.setdefault(key, set()).add(f.rel)
    names: list[NameReport] = []
    for key, n in counts.items():
        c = classify(ctx, key)
        names.append(
            NameReport(
                name_norm=key,
                bucket=c.bucket,
                count=n,
                raw_names=[name for name, _ in raw[key].most_common()],
                files=sorted(where[key]),
                target=c.target,
                target_kind=c.target_kind,
                note=c.note,
                hint=c.hint,
            )
        )
    names.sort(key=lambda r: (_BUCKET_RANK[r.bucket], -r.count, r.name_norm))
    return VocabularyReport(
        root=str(root),
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        files=len(files),
        parse_errors=errors,
        names=names,
    )


def default_report_path(settings: Settings | None = None, now: datetime | None = None) -> Path:
    """Where ``kerp recipes vocabulary`` writes without ``--out``: under ``data/vocabulary/``."""
    settings = settings or get_settings()
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return Path(settings.vocabulary_reports_path) / f"vocabulary-{stamp}.json"


def describe(n: NameReport) -> str:
    """One line for a name: what it is and where it should point."""
    if n.bucket == "drift":
        arrow = f"→ {n.target}"
        if n.target_kind == "standard":
            arrow += " (standard entry, not in the catalog yet)"
        if n.note:
            arrow += f", note “{n.note}”"
        return f"{n.name_norm} {arrow}"
    if n.bucket in ("conformant", "inflection") and n.target:
        return f"{n.name_norm} = {n.target}"
    if n.hint:
        return f"{n.name_norm} ({n.hint})"
    return n.name_norm


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def table(report: VocabularyReport) -> str:
    """The stdout view of a report."""
    lines = [
        f"{report.files} recipe files, {_plural(len(report.names), 'distinct name')}: "
        + ", ".join(f"{v} {k}" for k, v in report.counts.items() if v)
    ]
    for n in report.names:
        lines.append(
            f"  {n.bucket:<11} {n.count:>4}  {describe(n)}  [{_plural(len(n.files), 'file')}]"
        )
    for err in report.parse_errors:
        lines.append(f"  parse error      {err['path']}: {err['error']}")
    return "\n".join(lines)


# --- conform -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Rewrite:
    line: int  # 1-based
    old: str
    new: str


@dataclass
class FileChange:
    rel: str
    path: Path
    before: str
    after: str
    rewrites: list[Rewrite]  # one per changed line
    tokens: int  # how many ingredient tokens were rewritten


@dataclass
class ConformPlan:
    changes: list[FileChange]
    parse_errors: list[dict[str, str]]
    unsafe: list[dict[str, str]]  # files whose rewrite did not read back as intended; skipped

    @property
    def tokens(self) -> int:
        return sum(change.tokens for change in self.changes)


def _cased(raw_name: str, target: str) -> str:
    """The target in the token's own case: lowercase stays lowercase, Capitalized stays so."""
    if not target:
        return target
    if raw_name.islower() and target[:1].isupper() and not any(c.isupper() for c in target[1:]):
        return target[0].lower() + target[1:]
    if raw_name[:1].isupper() and raw_name[1:].islower() and target.islower():
        return target[0].upper() + target[1:]
    return target


def _single_word(name: str) -> bool:
    """Would the parser read ``@name`` with no braces as exactly this name?"""
    if not name or not name[0].isalnum() or not name[-1].isalnum():
        return False
    return all(c.isalnum() or c in _JOINERS for c in name)


def rewrite_token(line: str, ref: IngredientRef, new_name: str, note: str | None) -> str:
    """The line with one ingredient token renamed, and its note extended, in place.

    The marker (and `?`) in front of the name and the braces and note after it
    are kept byte for byte; `{}` is added when the token had none and the new
    name is more than a single word, or when a note has to be added (the parser
    reads a note only after `}`). A stripped prep note goes in front of an
    existing note: ``(minced; for the sauce)``.
    """
    start, end = ref.span
    name_start, name_end = ref.name_span
    head = line[start:name_start]
    tail = line[name_end:end]
    if "{" not in tail:
        tail = "{}" if (note or not _single_word(new_name)) else ""
    if note:
        close = tail.index("}")
        if close + 1 < len(tail) and tail[close + 1] == "(" and tail.endswith(")"):
            existing = tail[close + 2 : -1].strip()
            merged = f"{note}; {existing}" if existing else note
            tail = f"{tail[: close + 1]}({merged})"
        else:
            tail = f"{tail}({note})"
    return f"{line[:start]}{head}{new_name}{tail}{line[end:]}"


def _rewritten_text(
    text: str, recipe: Recipe, targets: dict[str, Classification]
) -> tuple[str, int]:
    """The file's text with every drifting ingredient token rewritten, and how many.

    Lines are split exactly as the parser splits them (``\\r\\n``, ``\\r`` or
    ``\\n``, a leading BOM set aside) so the recorded columns land on the right
    bytes; tokens on one line are rewritten right to left so earlier spans stay
    valid.
    """
    parts = _LINE_BREAK.split(text)
    lines = parts[0::2]
    bom = ""
    if lines and lines[0].startswith(_BOM):
        bom, lines[0] = _BOM, lines[0][len(_BOM) :]
    by_line: dict[int, list[IngredientRef]] = {}
    for ref in recipe.ingredients:
        key = normalize_name(ref.raw_name)
        if key in targets:
            by_line.setdefault(ref.line, []).append(ref)
    tokens = 0
    for number, refs in by_line.items():
        line = lines[number - 1]
        for ref in sorted(refs, key=lambda r: r.span[0], reverse=True):
            c = targets[normalize_name(ref.raw_name)]
            assert c.target is not None
            line = rewrite_token(line, ref, _cased(ref.raw_name, c.target), c.note)
            tokens += 1
        lines[number - 1] = line
    lines[0] = bom + lines[0]
    parts[0::2] = lines
    return "".join(parts), tokens


def _reads_back(before: Recipe, after: str, targets: dict[str, Classification]) -> bool:
    """The rewritten text parses, and each rewritten token reads as its target with its note."""
    parsed = parse(after)
    if isinstance(parsed, ParseError):
        return False
    old_refs = before.ingredients
    new_refs = parsed.ingredients
    if len(old_refs) != len(new_refs):
        return False
    for old, new in zip(old_refs, new_refs, strict=True):
        c = targets.get(normalize_name(old.raw_name))
        if c is None:
            if (old.raw_name, old.note, old.quantity, old.unit_text) != (
                new.raw_name,
                new.note,
                new.quantity,
                new.unit_text,
            ):
                return False
            continue
        assert c.target is not None
        if normalize_name(new.raw_name) != normalize_name(c.target):
            return False
        if (new.quantity, new.unit_text, new.optional) != (
            old.quantity,
            old.unit_text,
            old.optional,
        ):
            return False
        if c.note:
            expected = f"{c.note}; {old.note}" if old.note else c.note
            if new.note != expected:
                return False
        elif new.note != old.note:
            return False
    return True


def plan_conform(ctx: Context, root: Path, files: Iterable[CookFile]) -> ConformPlan:
    """Which files would change, and how. Reads only; nothing is written here."""
    plan = ConformPlan([], [], [])
    cache: dict[str, Classification] = {}
    for f in files:
        if isinstance(f.parsed, ParseError):
            plan.parse_errors.append({"path": f.rel, "error": str(f.parsed)})
            continue
        targets: dict[str, Classification] = {}
        for ref in f.parsed.ingredients:
            key = normalize_name(ref.raw_name)
            if not key:
                continue
            if key not in cache:
                cache[key] = classify(ctx, key)
            if cache[key].bucket == "drift":
                targets[key] = cache[key]
        if not targets:
            continue
        after, tokens = _rewritten_text(f.text, f.parsed, targets)
        if after == f.text:
            continue
        if not _reads_back(f.parsed, after, targets):
            plan.unsafe.append(
                {"path": f.rel, "error": "the rewrite did not read back as intended"}
            )
            continue
        rewrites = [
            Rewrite(number, old, new)
            for number, (old, new) in enumerate(
                zip(_LINE_BREAK.split(f.text)[0::2], _LINE_BREAK.split(after)[0::2], strict=True),
                start=1,
            )
            if old != new
        ]
        plan.changes.append(FileChange(f.rel, f.path, f.text, after, rewrites, tokens))
    return plan


def unified_diff(plan: ConformPlan) -> str:
    """The planned rewrites as one unified diff, the way ``git diff`` would show them."""
    out: list[str] = []
    for change in plan.changes:
        out.extend(
            difflib.unified_diff(
                change.before.splitlines(keepends=True),
                change.after.splitlines(keepends=True),
                fromfile=f"a/{change.rel}",
                tofile=f"b/{change.rel}",
            )
        )
    text = "".join(line if line.endswith("\n") else line + "\n" for line in out)
    return text


def apply_plan(plan: ConformPlan) -> list[str]:
    """Write every planned change back in place; the paths written. Never commits."""
    written: list[str] = []
    for change in plan.changes:
        # newline="" writes the text exactly as built: the file's own line breaks stay.
        with change.path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(change.after)
        written.append(change.rel)
    return written
