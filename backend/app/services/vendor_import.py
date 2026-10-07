"""Import a ``kitchen-erp-vendors/1`` or ``/2`` file: dry run, then apply (spec 03 §1F, Phase 2).

```
file ─▶ ≤ 5 MB ─▶ loader (no anchors or aliases, no floats) ─▶ VendorFile
      ─▶ structure (unique keys, parents that exist, no cycles, no stall
         across vendors or inside a stall) ──bad──▶ 422 bad_export, nothing written
  └─ok─▶ for each vendor: key ▸ name ─▶ its locations: key ▸ OSM ▸ name within 150 m
            ambiguous ─────────────▶ "unmatched", with what to do
            matched or new ─▶ geo cores + write_unless_edited
                              ─▶ created / updated / unchanged / conflict
       ─▶ dry run ? ROLLBACK : COMMIT ─▶ report
```

Rules for each field: a field is written only while it is empty or still
holds what a source last wrote there (``services.geo.write_unless_edited``);
otherwise it is a conflict, left alone and reported. Household fields are read
only from a household-mode file. Nothing is deleted, and no kitchen is ever
created: a name this deployment lacks is reported and the location keeps its
nearest-base default. Importing the same file twice changes nothing the
second time.

Text from a file is untrusted data: it is parsed against the schema, validated
by the same rules as the edit forms, and never interpreted.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import Integer, Numeric, String, cast, column, func, select, values
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ApiError
from app.models.geo import GeographyPoint, HomeBase, Place, Vendor, VendorLocation
from app.schemas.vendor_interchange import (
    FORMATS,
    V2_VENDOR_FIELDS,
    FieldChange,
    FieldConflict,
    ImportCounts,
    ImportItem,
    ImportReport,
    LocationEntry,
    VendorEntry,
    VendorFile,
)
from app.services import geo

MAX_BYTES = 5 * 1024 * 1024
MATCH_RADIUS_M = 150
NAME_SIMILARITY = Decimal("0.3")
SOURCE = "import"


def _bad(message: str, **details: Any) -> ApiError:
    return ApiError(422, "bad_export", message, details or None)


# --- reading -------------------------------------------------------------------


class _Loader(yaml.SafeLoader):
    """SafeLoader still expands anchors and aliases; this one refuses them."""

    def compose_node(self, parent: Any, index: Any) -> Any:  # type: ignore[override]
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise _bad("YAML anchors and aliases are not allowed in a vendor file.")
        return super().compose_node(parent, index)


def _no_floats(node: Any, path: str = "") -> None:
    if isinstance(node, float):
        raise _bad(f"{path or 'A value'} is a floating-point number; write it as a string.")
    if isinstance(node, dict):
        for k, v in node.items():
            _no_floats(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _no_floats(v, f"{path}[{i}]")


def _refuse_float(text: str) -> Any:
    raise _bad(f"{text} is a floating-point number; write numbers as strings.")


def parse(raw: bytes, fmt: str | None = None) -> VendorFile:
    """Bytes to a validated file, or 422 ``bad_export``; nothing is written."""
    if len(raw) > MAX_BYTES:
        raise _bad("The file is over the 5 MB limit.", limit_bytes=MAX_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _bad("The file is not UTF-8 text.") from None
    if fmt is None:
        fmt = "json" if text.lstrip().startswith(("{", "[")) else "yaml"
    try:
        if fmt == "json":
            data = json.loads(text, parse_float=_refuse_float, parse_constant=_refuse_float)
        else:
            data = yaml.load(text, Loader=_Loader)  # noqa: S506 - SafeLoader subclass
            _no_floats(data)
    except ApiError:
        raise
    except (ValueError, yaml.YAMLError) as exc:
        raise _bad(f"The file is not valid {fmt.upper()}: {exc}") from None
    if not isinstance(data, dict):
        raise _bad("The file must hold one document with a format, a source and vendors.")
    if data.get("format") not in FORMATS:
        raise _bad(
            f"Unsupported format {data.get('format')!r}; expected one of {', '.join(FORMATS)}."
        )
    try:
        file = VendorFile.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"at": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
            for e in exc.errors()[:20]
        ]
        raise _bad(
            f"The file does not match {data['format']}: {errors[0]['at']}: {errors[0]['problem']}",
            errors=errors,
        ) from None
    return file


async def check_structure(db: AsyncSession, file: VendorFile) -> None:
    """Keys, parents and household blocks that make sense, before anything is written."""
    vendor_keys: set[str] = set()
    owner: dict[str, str] = {}  # location key -> vendor key
    parents: dict[str, str] = {}
    for vendor in file.vendors:
        if vendor.key in vendor_keys:
            raise _bad(f"Vendor key {vendor.key} appears twice.", key=vendor.key)
        vendor_keys.add(vendor.key)
        for loc in vendor.locations:
            if loc.key in owner:
                raise _bad(f"Location key {loc.key} appears twice.", key=loc.key)
            owner[loc.key] = vendor.key
            if loc.parent is not None:
                parents[loc.key] = loc.parent
        if vendor.household is not None:
            if file.source.mode != "household":
                raise _bad("A public file carries household blocks.", key=vendor.key)
            stray = set(vendor.household.locations) - {loc.key for loc in vendor.locations}
            if stray:
                raise _bad(
                    f"The household block of {vendor.key} names locations it does not list.",
                    keys=sorted(stray),
                )
    outside = {p for p in parents.values() if p not in owner}
    known: dict[str, tuple[str, bool]] = {}  # key -> (vendor slug, is a stall)
    if outside:
        rows = await db.execute(
            select(VendorLocation.key, Vendor.slug, VendorLocation.parent_location_id)
            .join(Vendor, Vendor.id == VendorLocation.vendor_id)
            .where(VendorLocation.key.in_(outside))
        )
        known = {k: (slug, parent is not None) for k, slug, parent in rows.all()}
    for child, parent in parents.items():
        if parent == child:
            raise _bad(f"Location {child} is its own parent.", key=child)
        if parent in owner:
            parent_vendor, parent_is_stall = owner[parent], parent in parents
        elif parent in known:
            parent_vendor, parent_is_stall = known[parent]
        else:
            raise _bad(
                f"Location {child} names a parent, {parent}, that exists nowhere.", key=child
            )
        if parent_vendor != owner[child]:
            raise _bad(
                f"Stall {child} is inside {parent}, which belongs to another vendor.", key=child
            )
        if parent_is_stall:
            raise _bad(f"Stall {child} is inside {parent}, which is itself a stall.", key=child)


# --- matching ------------------------------------------------------------------


@dataclass
class _Match:
    location: VendorLocation | None = None
    reason: str | None = None  # set when ambiguous: no match, and none is created


async def _nearby_by_name(
    db: AsyncSession, vendor: Vendor, entries: list[LocationEntry]
) -> dict[int, list[uuid.UUID]]:
    """For each file location (by index): the vendor's locations within 150 m with a
    similar name. One query per vendor."""
    if not entries:
        return {}
    wanted = values(
        column("idx", Integer),
        column("lat", Numeric),
        column("lon", Numeric),
        column("name", String),
        name="wanted",
    ).data([(i, Decimal(e.lat), Decimal(e.lon), e.name) for i, e in enumerate(entries)])
    point = cast(
        func.ST_SetSRID(func.ST_MakePoint(wanted.c.lon, wanted.c.lat), 4326), GeographyPoint()
    )
    similar = func.similarity(func.lower(VendorLocation.name), func.lower(wanted.c.name))
    rows = await db.execute(
        select(wanted.c.idx, VendorLocation.id)
        .select_from(VendorLocation)
        .join(Place, Place.id == VendorLocation.place_id)
        .join(wanted, func.ST_DWithin(Place.geom, point, MATCH_RADIUS_M))
        .where(VendorLocation.vendor_id == vendor.id, similar >= NAME_SIMILARITY)
    )
    found: dict[int, list[uuid.UUID]] = {}
    for idx, location_id in rows.all():
        found.setdefault(idx, []).append(location_id)
    return found


async def _match_locations(
    db: AsyncSession, vendor: Vendor | None, entries: list[LocationEntry]
) -> list[_Match]:
    """Key, then OSM type and id, then name within 150 m. Two at one step is ambiguous."""
    keys = [e.key for e in entries]
    osm = [(e.osm.type, e.osm.id) for e in entries if e.osm is not None]
    by_key: dict[str, VendorLocation] = {}
    by_osm: dict[tuple[str, int], VendorLocation] = {}
    if keys:
        by_key = {
            loc.key: loc
            for loc in (
                await db.execute(select(VendorLocation).where(VendorLocation.key.in_(keys)))
            )
            .unique()
            .scalars()
        }
    if osm:
        by_osm = {
            (loc.osm_type, loc.osm_id): loc  # type: ignore[misc]
            for loc in (
                await db.execute(
                    select(VendorLocation).where(VendorLocation.osm_id.in_({i for _, i in osm}))
                )
            )
            .unique()
            .scalars()
        }
    nearby = await _nearby_by_name(db, vendor, entries) if vendor is not None else {}
    matches: list[_Match] = []
    claimed: set[uuid.UUID] = set()
    for idx, entry in enumerate(entries):
        match = _Match()
        found = by_key.get(entry.key)
        if found is None and entry.osm is not None:
            found = by_osm.get((entry.osm.type, entry.osm.id))
        if found is not None and (vendor is None or found.vendor_id != vendor.id):
            match.reason = (
                f"Its key or OpenStreetMap place belongs to a location of {found.vendor.name}; "
                "fix the file or move that location first."
            )
        elif found is not None:
            match.location = found
        else:
            near = nearby.get(idx, [])
            if len(near) == 1:
                match.location = await db.get(VendorLocation, near[0])
            elif len(near) > 1:
                match.reason = (
                    f"Matches {len(near)} locations within {MATCH_RADIUS_M} m; add a key "
                    "or link it to OpenStreetMap."
                )
        if match.location is not None:
            if match.location.id in claimed:
                match = _Match(reason="Another entry in the file matched the same location.")
            else:
                claimed.add(match.location.id)
        matches.append(match)
    return matches


# --- writing --------------------------------------------------------------------


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass
class _Row:
    item: ImportItem
    changes: list[FieldChange] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)

    def write(
        self, obj: Any, name: str, value: Any, *, ref: str, now: datetime, label: str | None = None
    ) -> None:
        old = getattr(obj, name)
        outcome = geo.write_unless_edited(obj, name, value, source=SOURCE, ref=ref, now=now)
        if outcome in ("filled", "updated"):
            self.changes.append(FieldChange(field=label or name, old=_text(old), new=_text(value)))
        elif outcome == "kept":
            self.conflicts.append(
                FieldConflict(field=label or name, current=_text(old), file=_text(value))
            )

    def record(self, obj: Any, names: tuple[str, ...], *, ref: str, now: datetime) -> None:
        """A row just created from the file: its values came from the import."""
        for name in names:
            value = getattr(obj, name)
            if value is not None:
                geo.write_unless_edited(obj, name, value, source=SOURCE, ref=ref, now=now)
                self.changes.append(FieldChange(field=name, old=None, new=_text(value)))

    def done(self) -> ImportItem:
        item = self.item
        item.changes, item.conflicts = self.changes, self.conflicts
        if item.outcome not in ("created", "unmatched"):
            item.outcome = (
                "conflict" if self.conflicts else "updated" if self.changes else "unchanged"
            )
        return item


VENDOR_FIELDS = ("name", "kind", "price_scope", "website", "brand", "wikidata")
LOCATION_FIELDS = ("name", "address", "phone", "opening_hours")


def _v2_vendor_values(entry: VendorEntry) -> list[tuple[str, Any]]:
    """The /2 product facts a file gives (1H), as stored; absent ones are left out."""
    values: list[tuple[str, Any]] = []
    if entry.platform is not None:
        values.append(("platform", entry.platform))
    if entry.fetch_policy is not None:
        values.append(("fetch_policy", entry.fetch_policy))
    if entry.rw_layout is not None:
        values.append(("rw_layout", entry.rw_layout.model_dump()))
    if entry.code_position is not None:
        values.append(("code_position", entry.code_position.model_dump()))
    return values


def _clean_location_values(entry: LocationEntry) -> dict[str, Any]:
    """The same checks the edit form applies (names, phones, opening hours)."""
    return {
        "name": geo._clean(entry.name, required=True),
        "address": geo._clean(entry.address),
        "phone": geo._phone(entry.phone),
        "opening_hours": geo._hours(entry.opening_hours),
    }


@dataclass
class _Context:
    db: AsyncSession
    file: VendorFile
    ref: str
    now: datetime
    home_bases: dict[str, uuid.UUID]
    unresolved: set[str] = field(default_factory=set)
    created_locations: dict[str, VendorLocation] = field(default_factory=dict)

    @property
    def household(self) -> bool:
        return self.file.source.mode == "household"


async def _key_free(db: AsyncSession, column_: Any, key: str) -> bool:
    return (await db.scalar(select(func.count()).where(column_ == key))) == 0


async def _import_vendor(ctx: _Context, entry: VendorEntry) -> list[ImportItem]:
    db = ctx.db
    vendor = (
        await db.execute(
            select(Vendor).options(selectinload(Vendor.locations)).where(Vendor.slug == entry.key)
        )
    ).scalar_one_or_none()
    if vendor is None:
        vendor = await geo.find_vendor_by_name(db, entry.name)
    house = entry.household if ctx.household else None
    row = _Row(ImportItem(target="vendor", key=entry.key, name=entry.name, outcome="unchanged"))
    if vendor is None:
        vendor = await geo.create_vendor_row(
            db,
            name=entry.name,
            kind=entry.kind,
            price_scope=entry.price_scope,
            website=entry.website,
            brand=entry.brand,
            wikidata=entry.wikidata,
            notes=house.notes if house else None,
            active=house.active if house else True,
            slug=entry.key if await _key_free(db, Vendor.slug, entry.key) else None,
        )
        for fname, value in _v2_vendor_values(entry):
            setattr(vendor, fname, value)
        row.item.outcome = "created"
        row.record(vendor, VENDOR_FIELDS + V2_VENDOR_FIELDS, ref=ctx.ref, now=ctx.now)
    else:
        name = geo._clean(entry.name, required=True)
        for fname, value in (
            ("name", name),
            ("kind", entry.kind),
            ("price_scope", entry.price_scope),
            ("website", geo._clean(entry.website)),
            ("brand", geo._clean(entry.brand)),
            ("wikidata", entry.wikidata),
        ):
            # A field the file leaves out is left alone: import never clears one.
            if value is not None:
                row.write(vendor, fname, value, ref=ctx.ref, now=ctx.now)
        for fname, value in _v2_vendor_values(entry):
            row.write(vendor, fname, value, ref=ctx.ref, now=ctx.now)
        if house is not None:
            if house.notes is not None:
                row.write(vendor, "notes", house.notes, ref=ctx.ref, now=ctx.now)
            row.write(vendor, "active", house.active, ref=ctx.ref, now=ctx.now)
    await geo._flush(db)
    items = [row.done()]

    # Parents first, so a stall can name a market created from the same file.
    ordered = sorted(entry.locations, key=lambda e: e.parent is not None)
    matches = await _match_locations(db, vendor, ordered)
    for loc_entry, match in zip(ordered, matches, strict=True):
        items.append(await _import_location(ctx, vendor, entry, loc_entry, match))
    return items


async def _import_location(
    ctx: _Context, vendor: Vendor, vendor_entry: VendorEntry, entry: LocationEntry, match: _Match
) -> ImportItem:
    db = ctx.db
    base = ImportItem(
        target="location",
        key=entry.key,
        name=entry.name,
        vendor_key=vendor_entry.key,
        outcome="unchanged",
    )
    if match.reason is not None:
        base.outcome, base.reason = "unmatched", match.reason
        return base
    house = (
        vendor_entry.household.locations.get(entry.key)
        if ctx.household and vendor_entry.household is not None
        else None
    )
    home_id: uuid.UUID | None = None
    if house is not None and house.home_base is not None:
        home_id = ctx.home_bases.get(house.home_base)
        if home_id is None:
            ctx.unresolved.add(house.home_base)
    clean = _clean_location_values(entry)
    row = _Row(base)
    location = match.location
    if location is None:
        osm_free = (
            entry.osm is not None
            and (
                await db.scalar(
                    select(func.count()).where(
                        VendorLocation.osm_type == entry.osm.type,
                        VendorLocation.osm_id == entry.osm.id,
                    )
                )
            )
            == 0
        )
        data: dict[str, Any] = {
            **clean,
            "lat": Decimal(entry.lat),
            "lon": Decimal(entry.lon),
            "key": entry.key if await _key_free(db, VendorLocation.key, entry.key) else None,
        }
        if osm_free and entry.osm is not None:
            data["osm_type"], data["osm_id"] = entry.osm.type, entry.osm.id
        if entry.parent is not None:
            parent = (
                ctx.created_locations.get(entry.parent)
                or (
                    await db.execute(
                        select(VendorLocation).where(VendorLocation.key == entry.parent)
                    )
                )
                .unique()
                .scalar_one_or_none()
            )
            data["parent_location_id"] = parent.id if parent is not None else None
        if home_id is not None:
            data["home_base_id"] = home_id
        if house is not None:
            data.update(
                stop_overhead_min=house.stop_overhead_min,
                receipt_identifiers=house.receipt_identifiers,
                publishable=house.publishable,
                active=house.active,
            )
        location = await geo.create_location_row(db, data, vendor=vendor)
        ctx.created_locations[entry.key] = location
        if entry.platform_store_ref is not None:
            location.platform_store_ref = entry.platform_store_ref
        row.item.outcome = "created"
        row.record(location, LOCATION_FIELDS + ("platform_store_ref",), ref=ctx.ref, now=ctx.now)
        return row.done()

    for fname in LOCATION_FIELDS:
        value = clean[fname]
        if value is not None:
            row.write(location, fname, value, ref=ctx.ref, now=ctx.now)
    if entry.platform_store_ref is not None:
        row.write(
            location, "platform_store_ref", entry.platform_store_ref, ref=ctx.ref, now=ctx.now
        )
    if entry.osm is not None and (location.osm_type, location.osm_id) != (
        entry.osm.type,
        entry.osm.id,
    ):
        file_osm = f"{entry.osm.type}/{entry.osm.id}"
        if location.osm_type is None:
            location.osm_type, location.osm_id = entry.osm.type, entry.osm.id
            row.changes.append(FieldChange(field="osm", old=None, new=file_osm))
        else:
            row.conflicts.append(
                FieldConflict(
                    field="osm", current=f"{location.osm_type}/{location.osm_id}", file=file_osm
                )
            )
    if house is not None:
        for fname in ("stop_overhead_min", "publishable", "active"):
            value = getattr(house, fname)
            if value is not None:
                row.write(location, fname, value, ref=ctx.ref, now=ctx.now)
        missing = [c for c in house.receipt_identifiers if c not in location.receipt_identifiers]
        if missing:
            old = ", ".join(location.receipt_identifiers) or None
            location.receipt_identifiers = [*location.receipt_identifiers, *missing]
            row.changes.append(
                FieldChange(
                    field="receipt_identifiers",
                    old=old,
                    new=", ".join(location.receipt_identifiers),
                )
            )
        if home_id is not None and location.home_base_id != home_id:
            if location.home_base_id is None:
                location.home_base_id = home_id
                row.changes.append(FieldChange(field="home_base", old=None, new=house.home_base))
            else:
                current = next(
                    (n for n, i in ctx.home_bases.items() if i == location.home_base_id), None
                )
                row.conflicts.append(
                    FieldConflict(field="home_base", current=current, file=house.home_base)
                )
    await geo._flush(db)
    return row.done()


async def run(
    db: AsyncSession, raw: bytes, *, fmt: str | None, dry_run: bool, filename: str
) -> ImportReport:
    """Parse, check, match and write in one transaction; commit, or roll back for a dry run."""
    file = parse(raw, fmt)
    await check_structure(db, file)
    home_bases = {h.name: h.id for h in (await db.execute(select(HomeBase))).unique().scalars()}
    ctx = _Context(
        db=db,
        file=file,
        ref=filename[:200] or "vendor file",
        now=datetime.now(UTC),
        home_bases=home_bases,
    )
    items: list[ImportItem] = []
    try:
        for vendor_entry in file.vendors:
            items.extend(await _import_vendor(ctx, vendor_entry))
        if dry_run:
            await db.rollback()
        else:
            await db.commit()
    except BaseException:
        await db.rollback()
        raise
    counts = ImportCounts(
        created=sum(i.outcome == "created" for i in items),
        updated=sum(i.outcome == "updated" for i in items),
        unchanged=sum(i.outcome == "unchanged" for i in items),
        conflicts=sum(i.outcome == "conflict" for i in items),
        unmatched=sum(i.outcome == "unmatched" for i in items),
    )
    return ImportReport(
        dry_run=dry_run,
        mode=file.source.mode,
        counts=counts,
        items=items,
        unresolved_home_bases=sorted(ctx.unresolved),
    )
