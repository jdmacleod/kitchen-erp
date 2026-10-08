"""Export and import a ``kitchen-erp-ingredients/1`` file (spec 03 §1G, #241).

```
export: every ingredient, by key ─▶ IngredientFile ─▶ JSON or YAML
import: file ─▶ interchange.load (≤ 5 MB, no aliases, no floats, known format)
      ─▶ IngredientFile ──bad──▶ 422 bad_export, nothing written
  └─ok─▶ for each entry: merged in the file? ─▶ skipped
            match: key ▸ name ▸ spelling ─▶ merged here? ─▶ skipped
            matched or new ─▶ fields through interchange.write_unless_edited
                              spellings, USDA ids, measures added, never removed
                              ─▶ created / updated / unchanged / conflict
       ─▶ dry run ? ROLLBACK : COMMIT ─▶ re-normalize prices where a bridge changed
```

The rules are vendor import's (1F). A field is written only while it is empty
or still holds what a file last wrote there; otherwise it is a conflict, left
alone and reported, so a household's own edits always win. An ingredient this
import creates counts as the file's, so a later file may still update it until a
person edits it. Nothing is deleted, deactivated, renamed or merged: those are a
person's acts. Importing the same file twice changes nothing the second time.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog import standard
from app.catalog.names import normalize_name
from app.catalog.standard_match import match_entry
from app.core.errors import ApiError
from app.models.catalog import Ingredient, IngredientAlias, IngredientMeasure, IngredientRef
from app.schemas.ingredient_interchange import (
    FORMAT,
    FORMATS,
    Density,
    FieldChange,
    FieldConflict,
    FileSource,
    ImportCounts,
    ImportItem,
    ImportReport,
    IngredientEntry,
    IngredientFile,
    Measure,
)
from app.services import interchange
from app.services.catalog import set_preferred_fdc
from app.services.pricebook import recompute_for_ingredient
from app.services.spellings import add_generated_spellings, add_spelling

SOURCE_NAME = "kitchen-erp"
SOURCE = "import"
# Spellings an export carries: curated ones and old names. Plurals are generated
# again on import.
EXPORTED_ALIAS_KINDS = ("synonym", "legacy")
LOCAL_PREFIX = "local."


def _text(value: Any) -> str | None:
    """A field's value as the file and ``field_source`` hold it: Decimals as plain strings."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        return "0" if text in ("-0", "") else text
    return str(value)


# --- export --------------------------------------------------------------------


async def _all(db: AsyncSession) -> list[Ingredient]:
    return list(
        (
            await db.execute(
                select(Ingredient)
                .options(selectinload(Ingredient.measures))
                .order_by(Ingredient.slug)
            )
        )
        .unique()
        .scalars()
    )


async def build(db: AsyncSession, *, now: datetime | None = None) -> IngredientFile:
    ingredients = await _all(db)
    keys = {i.id: i.slug for i in ingredients}
    spellings: dict[uuid.UUID, list[str]] = {}
    for alias in (
        await db.execute(
            select(IngredientAlias)
            .where(IngredientAlias.kind.in_(EXPORTED_ALIAS_KINDS))
            .order_by(IngredientAlias.name_norm)
        )
    ).scalars():
        spellings.setdefault(alias.ingredient_id, []).append(alias.name_norm)
    refs: dict[uuid.UUID, list[int]] = {}
    for ref in (
        await db.execute(
            select(IngredientRef)
            .where(IngredientRef.system == "fdc")
            .order_by(IngredientRef.is_preferred.desc(), IngredientRef.external_id)
        )
    ).scalars():
        refs.setdefault(ref.ingredient_id, []).append(int(ref.external_id))
    entries = [
        IngredientEntry(
            key=i.slug,
            name=i.name,
            category=i.category,
            unit=i.canonical_unit,  # type: ignore[arg-type]
            density=(
                Density(
                    g_per_ml=i.density_g_per_ml,
                    source=i.density_source,  # type: ignore[arg-type]
                    confirmed=i.density_confirmed,
                )
                if i.density_g_per_ml is not None and i.density_source is not None
                else None
            ),
            yield_pct=i.yield_pct,
            perishability=i.perishability,  # type: ignore[arg-type]
            notes=i.notes,
            spellings=spellings.get(i.id, []),
            fdc=refs.get(i.id, []),
            measures=[
                Measure(
                    label=m.label,
                    qty=m.canonical_qty,
                    source=m.source,  # type: ignore[arg-type]
                    confirmed=m.confirmed,
                )
                for m in sorted(i.measures, key=lambda m: m.label.lower())
            ],
            active=i.active,
            merged_into=keys.get(i.merged_into) if i.merged_into is not None else None,
        )
        for i in ingredients
    ]
    return IngredientFile(
        format=FORMAT,
        source=FileSource(name=SOURCE_NAME, exported_at=now or datetime.now(UTC)),
        ingredients=entries,
    )


def to_document(file: IngredientFile) -> dict[str, Any]:
    """Plain data: numbers as strings, empty optional fields left out."""
    document = file.model_dump(mode="json", exclude_none=True)
    for entry in document["ingredients"]:
        # Pydantic writes Decimals as strings already; keep them in their shortest form.
        if "yield_pct" in entry:
            entry["yield_pct"] = _text(Decimal(entry["yield_pct"]))
        if "density" in entry:
            entry["density"]["g_per_ml"] = _text(Decimal(entry["density"]["g_per_ml"]))
        for measure in entry.get("measures", []):
            measure["qty"] = _text(Decimal(measure["qty"]))
        for name in ("spellings", "fdc", "measures"):
            if not entry.get(name):
                entry.pop(name, None)
    return document


def render(file: IngredientFile, fmt: str) -> str:
    document = to_document(file)
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)


# --- import --------------------------------------------------------------------


def parse(raw: bytes, fmt: str | None = None) -> IngredientFile:
    """Bytes to a validated file, or 422 ``bad_export``; nothing is written."""
    data = interchange.load(
        raw,
        fmt,
        kind="an ingredient file",
        formats=FORMATS,
        shape="a format, a source and ingredients",
    )
    file = interchange.validate(IngredientFile, data)
    keys = [e.key for e in file.ingredients]
    if len(set(keys)) != len(keys):
        duplicate = next(k for k in keys if keys.count(k) > 1)
        raise interchange.bad_file(f"The key {duplicate!r} appears more than once.")
    return file


@dataclass
class _Context:
    db: AsyncSession
    ref: str
    now: datetime
    rebridge: set[uuid.UUID] = field(default_factory=set)


@dataclass
class _Row:
    ctx: _Context
    ingredient: Ingredient
    entry: IngredientEntry
    created: bool
    changes: list[FieldChange] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)

    def write(self, name: str, value: Any, *, label: str | None = None) -> str:
        """One field through the edit-wins rule; records what happened."""
        owned_by_file = self.created

        def last_written(obj: Any, attr: str) -> Any:
            # A row this import created is the file's own until a person edits it.
            return _text(getattr(obj, attr)) if owned_by_file else interchange.recorded(obj, attr)

        before = getattr(self.ingredient, name)
        outcome = interchange.write_unless_edited(
            self.ingredient,
            name,
            value,
            source=SOURCE,
            ref=self.ctx.ref,
            now=self.ctx.now,
            last_written=last_written,
            stored=_text,
        )
        shown = label or name
        if outcome in ("filled", "updated"):
            if not self.created:
                self.changes.append(FieldChange(field=shown, value=_text(value)))
        elif outcome == "kept":
            self.conflicts.append(
                FieldConflict(field=shown, current=_text(before), file=_text(value))
            )
        return outcome

    def item(self, outcome_if_quiet: str = "unchanged") -> ImportItem:
        if self.created:
            outcome = "created"
        elif self.conflicts:
            outcome = "conflict"
        elif self.changes:
            outcome = "updated"
        else:
            outcome = outcome_if_quiet
        return ImportItem(
            key=self.entry.key,
            name=self.ingredient.name,
            outcome=outcome,  # type: ignore[arg-type]
            changes=self.changes,
            conflicts=self.conflicts,
        )


async def _match(db: AsyncSession, entry: IngredientEntry) -> Ingredient | None:
    """The ingredient an entry describes: by key, then name, then a spelling."""
    found = (await db.execute(select(Ingredient).where(Ingredient.slug == entry.key))).scalar()
    if found is not None:
        return found
    found = (
        await db.execute(
            select(Ingredient).where(func.lower(Ingredient.name) == entry.name.lower())
        )
    ).scalar()
    if found is not None:
        return found
    norm = normalize_name(entry.name)
    if not norm:
        return None
    holder = (
        await db.execute(
            select(IngredientAlias.ingredient_id).where(IngredientAlias.name_norm == norm)
        )
    ).scalar()
    return None if holder is None else await db.get(Ingredient, holder)


async def _create(db: AsyncSession, entry: IngredientEntry) -> Ingredient:
    """A new ingredient named and keyed as the file says; its fields follow."""
    is_standard = standard.entry(entry.key) is not None
    taken = (await db.execute(select(Ingredient.id).where(Ingredient.slug == entry.key))).scalar()
    keep_key = taken is None and (is_standard or entry.key.startswith(LOCAL_PREFIX))
    ingredient = Ingredient(
        name=entry.name,
        # Any other key could someday equal a standard one: a fresh local key is made.
        slug=entry.key if keep_key else "",
        reconcile_state=(
            "linked"
            if is_standard and keep_key
            else ("unreviewed" if match_entry(entry.name) is not None else "not_applicable")
        ),
        canonical_unit=entry.unit,
        active=entry.active,
        field_source={},
    )
    db.add(ingredient)
    await db.flush()
    await add_generated_spellings(db, ingredient)
    return ingredient


async def _spellings(row: _Row) -> None:
    db, ingredient = row.ctx.db, row.ingredient
    own = normalize_name(ingredient.name)
    for spelling in row.entry.spellings:
        norm = normalize_name(spelling)
        if not norm or norm == own:
            continue
        holder = (
            await db.execute(
                select(IngredientAlias.ingredient_id).where(IngredientAlias.name_norm == norm)
            )
        ).scalar()
        if holder == ingredient.id:
            continue
        if holder is not None:
            row.conflicts.append(
                FieldConflict(field="spelling", current="another ingredient's", file=spelling)
            )
            continue
        try:
            async with db.begin_nested():
                await add_spelling(db, ingredient.id, spelling, kind="synonym", source=SOURCE)
        except ApiError:
            row.conflicts.append(
                FieldConflict(field="spelling", current="another ingredient's", file=spelling)
            )
            continue
        if not row.created:
            row.changes.append(FieldChange(field="spelling", value=spelling))


async def _usda(row: _Row) -> None:
    if not row.entry.fdc:
        return
    db, ingredient = row.ctx.db, row.ingredient
    refs = (
        (
            await db.execute(
                select(IngredientRef).where(
                    IngredientRef.ingredient_id == ingredient.id, IngredientRef.system == "fdc"
                )
            )
        )
        .scalars()
        .all()
    )
    have = {r.external_id for r in refs}
    preferred = next((r.external_id for r in refs if r.is_preferred), None)
    wanted = str(row.entry.fdc[0])
    if preferred is None:
        await set_preferred_fdc(db, ingredient.id, row.entry.fdc[0])
        have.add(wanted)
        if not row.created:
            row.changes.append(FieldChange(field="usda", value=wanted))
    elif preferred != wanted:
        row.conflicts.append(FieldConflict(field="usda", current=preferred, file=wanted))
    for fdc_id in row.entry.fdc[1:]:
        if str(fdc_id) not in have:
            db.add(
                IngredientRef(
                    ingredient_id=ingredient.id,
                    system="fdc",
                    external_id=str(fdc_id),
                    is_preferred=False,
                )
            )
            have.add(str(fdc_id))
    await db.flush()


async def _measures(row: _Row) -> None:
    db, ingredient = row.ctx.db, row.ingredient
    have = {
        m.label.lower(): m
        for m in (
            await db.execute(
                select(IngredientMeasure).where(IngredientMeasure.ingredient_id == ingredient.id)
            )
        ).scalars()
    }
    for measure in row.entry.measures:
        current = have.get(measure.label.lower())
        if current is None:
            db.add(
                IngredientMeasure(
                    ingredient_id=ingredient.id,
                    label=measure.label,
                    canonical_qty=measure.qty,
                    source=measure.source,
                    confirmed=measure.confirmed,
                )
            )
            if not row.created:
                row.changes.append(
                    FieldChange(field=f"measure {measure.label}", value=_text(measure.qty))
                )
        elif current.canonical_qty != measure.qty:
            # A measure has no provenance to tell a person's edit from a file's: keep it.
            row.conflicts.append(
                FieldConflict(
                    field=f"measure {measure.label}",
                    current=_text(current.canonical_qty),
                    file=_text(measure.qty),
                )
            )
    await db.flush()


async def _apply(ctx: _Context, entry: IngredientEntry) -> ImportItem:
    if entry.merged_into is not None:
        return ImportItem(
            key=entry.key,
            name=entry.name,
            outcome="skipped",
            reason=f"merged into {entry.merged_into} in the file; merging is done by a person",
        )
    db = ctx.db
    ingredient = await _match(db, entry)
    if ingredient is not None and ingredient.merged_into is not None:
        survivor = await db.get(Ingredient, ingredient.merged_into)
        into = survivor.name if survivor else "another ingredient"
        return ImportItem(
            key=entry.key,
            name=ingredient.name,
            outcome="skipped",
            reason=f"already merged here into {into}",
        )
    created = ingredient is None
    if ingredient is None:
        ingredient = await _create(db, entry)
    row = _Row(ctx=ctx, ingredient=ingredient, entry=entry, created=created)
    if entry.category is not None:
        row.write("category", entry.category)
    unit = row.write("canonical_unit", entry.unit, label="unit")
    row.write("yield_pct", entry.yield_pct)
    row.write("perishability", entry.perishability)
    if entry.notes is not None:
        row.write("notes", entry.notes)
    density = "unchanged"
    if entry.density is not None:
        density = row.write("density_g_per_ml", entry.density.g_per_ml, label="density")
        if density in ("filled", "updated"):
            ingredient.density_source = entry.density.source
            ingredient.density_confirmed = entry.density.confirmed
    await db.flush()
    await _spellings(row)
    await _usda(row)
    await _measures(row)
    if not created and {unit, density} & {"filled", "updated"}:
        ctx.rebridge.add(ingredient.id)
    return row.item()


async def run(
    db: AsyncSession, raw: bytes, *, fmt: str | None, dry_run: bool, filename: str
) -> ImportReport:
    """Parse, match and write in one transaction; commit, or roll back for a dry run."""
    file = parse(raw, fmt)
    ctx = _Context(db=db, ref=filename[:200] or "ingredient file", now=datetime.now(UTC))
    items: list[ImportItem] = []
    try:
        for entry in file.ingredients:
            items.append(await _apply(ctx, entry))
        if dry_run:
            await db.rollback()
        else:
            await db.commit()
    except BaseException:
        await db.rollback()
        raise
    if not dry_run:
        # A changed density or unit changes what every price of its products
        # normalizes to (non-negotiable 5): recompute them from the facts.
        for ingredient_id in sorted(ctx.rebridge):
            await recompute_for_ingredient(db, ingredient_id)
    counts = ImportCounts(
        created=sum(i.outcome == "created" for i in items),
        updated=sum(i.outcome == "updated" for i in items),
        unchanged=sum(i.outcome == "unchanged" for i in items),
        conflicts=sum(i.outcome == "conflict" for i in items),
        skipped=sum(i.outcome == "skipped" for i in items),
    )
    return ImportReport(dry_run=dry_run, counts=counts, items=items)
