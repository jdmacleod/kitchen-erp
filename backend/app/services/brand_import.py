"""``kerp import brands``: a kitchen-erp-brands/1 bundle into the brand tables (2R).

Rows match by key. A field a person edited keeps the person's value
(``write_unless_edited``). Banners, aliases and carried labels are the dataset's
own and are replaced as the file has them. A family or brand the file no longer
lists is reported, never deleted, because products point at brands. An alias
whose key another brand already holds is refused by line, and the rest of the
file is still imported. Afterwards every vendor and product is linked again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.brand_names import name_key
from app.models.brands import Brand, BrandAlias, BrandFamily, BrandFamilyBanner, BrandFamilyCarries
from app.models.catalog import Product
from app.models.geo import Vendor
from app.schemas.brand_interchange import (
    FORMATS,
    BrandEntry,
    BrandFile,
    BrandImportCounts,
    BrandImportReport,
    FamilyEntry,
    Refusal,
    VendorLink,
)
from app.services import brands, interchange

FAMILY_FIELDS = ("name", "kind", "wikidata", "owner_name", "owner_wikidata")
BRAND_FIELDS = ("name", "tier", "since", "until")


def parse(raw: bytes, fmt: str | None = None) -> BrandFile:
    data = interchange.load(
        raw, fmt, kind="a brand file", formats=FORMATS, shape="a format and families"
    )
    return interchange.validate(BrandFile, data)


class _Writer:
    def __init__(self, counts: BrandImportCounts, ref: str | None, now: datetime) -> None:
        self.counts, self.ref, self.now = counts, ref, now

    def fields(self, obj: Any, values: dict[str, Any]) -> bool:
        """Write each value unless edited; True if anything changed."""
        changed = False
        for name, value in values.items():
            outcome = interchange.write_unless_edited(
                obj, name, value, source=brands.SOURCE, ref=self.ref, now=self.now
            )
            if outcome == "kept":
                self.counts.kept += 1
            changed |= outcome in ("filled", "updated")
        return changed


async def _family(db: AsyncSession, w: _Writer, entry: FamilyEntry) -> BrandFamily:
    family = (
        await db.execute(select(BrandFamily).where(BrandFamily.key == entry.key))
    ).scalar_one_or_none()
    values = {
        "name": entry.name,
        "kind": entry.kind,
        "wikidata": entry.wikidata,
        "owner_name": entry.owner.name if entry.owner else None,
        "owner_wikidata": entry.owner.wikidata if entry.owner else None,
    }
    if family is None:
        family = BrandFamily(id=uuid.uuid4(), key=entry.key, field_source={}, sources=[])
        family.name, family.kind = entry.name, entry.kind
        db.add(family)
        w.fields(family, values)
        w.counts.families_created += 1
    elif w.fields(family, values):
        w.counts.families_updated += 1
    else:
        w.counts.unchanged += 1
    family.sources = [s.model_dump() for s in entry.sources]
    await db.flush()
    await db.execute(delete(BrandFamilyBanner).where(BrandFamilyBanner.family_id == family.id))
    for b in entry.banners:
        db.add(
            BrandFamilyBanner(
                family_id=family.id, key=b.key, name=b.name, wikidata=b.wikidata, domains=b.domains
            )
        )
    return family


async def _brand(db: AsyncSession, w: _Writer, family: BrandFamily, entry: BrandEntry) -> Brand:
    brand = (await db.execute(select(Brand).where(Brand.key == entry.key))).scalar_one_or_none()
    values = {"name": entry.name, "tier": entry.tier, "since": entry.since, "until": entry.until}
    if brand is None:
        brand = Brand(id=uuid.uuid4(), key=entry.key, family_id=family.id, field_source={})
        brand.name, brand.tier = entry.name, entry.tier
        db.add(brand)
        w.fields(brand, values)
        w.counts.brands_created += 1
    elif w.fields(brand, values):
        w.counts.brands_updated += 1
    else:
        w.counts.unchanged += 1
    brand.family_id = family.id
    brand.categories = list(entry.categories)
    await db.flush()
    return brand


async def _aliases(
    db: AsyncSession, brand: Brand, entry: BrandEntry, refused: list[Refusal]
) -> None:
    """The brand's spellings as the file has them; one another brand holds is refused."""
    wanted: dict[str, str] = {}
    for spelling in (entry.name, *entry.aliases):
        if (key := name_key(spelling)) and key not in wanted:
            wanted[key] = spelling
    held = {
        a.alias_key: a.brand_id
        for a in (
            await db.execute(select(BrandAlias).where(BrandAlias.alias_key.in_(list(wanted))))
        ).scalars()
    }
    await db.execute(delete(BrandAlias).where(BrandAlias.brand_id == brand.id))
    for key, spelling in wanted.items():
        owner = held.get(key)
        if owner is not None and owner != brand.id:
            refused.append(
                Refusal(key=entry.key, reason=f"{spelling!r} already names another brand")
            )
            continue
        db.add(BrandAlias(brand_id=brand.id, alias=spelling, alias_key=key))
    await db.flush()


async def run(
    db: AsyncSession, raw: bytes, *, fmt: str | None = None, dry_run: bool = False
) -> BrandImportReport:
    """Import a bundle in one transaction; a dry run rolls it back and reports the same."""
    file = parse(raw, fmt)
    now = datetime.now(UTC)
    counts = BrandImportCounts()
    commit = file.source.commit if file.source else None
    w = _Writer(counts, commit, now)
    refused: list[Refusal] = []
    by_key: dict[str, Brand] = {}
    families: dict[str, BrandFamily] = {}
    try:
        for entry in file.families:
            family = await _family(db, w, entry)
            families[entry.key] = family
            for b in entry.brands:
                brand = await _brand(db, w, family, b)
                by_key[b.key] = brand
                await _aliases(db, brand, b, refused)
        known = {b.key: b for b in (await db.execute(select(Brand))).scalars()}
        for entry in file.families:
            for b in entry.brands:
                target = known.get(b.replaced_by) if b.replaced_by else None
                if b.replaced_by and target is None:
                    refused.append(Refusal(key=b.key, reason=f"no brand {b.replaced_by!r}"))
                by_key[b.key].replaced_by_id = target.id if target else None
            family = families[entry.key]
            await db.execute(
                delete(BrandFamilyCarries).where(BrandFamilyCarries.family_id == family.id)
            )
            for carried in dict.fromkeys(entry.carries):
                if (target := known.get(carried)) is None:
                    refused.append(Refusal(key=entry.key, reason=f"carries no brand {carried!r}"))
                    continue
                db.add(BrandFamilyCarries(family_id=family.id, brand_id=target.id))
        await db.flush()

        listed = {e.key for e in file.families} | set(by_key)
        here = [f.key for f in (await db.execute(select(BrandFamily))).scalars()]
        missing = sorted(k for k in [*here, *known] if k not in listed)

        linked, needing = await _link_vendors(db, now)
        products = await _link_products(db)
        report = BrandImportReport(
            dry_run=dry_run,
            commit=commit,
            counts=counts,
            missing=missing,
            refused=refused,
            vendors_linked=linked,
            vendors_needing_you=needing,
            products_linked=products,
        )
        if dry_run:
            await db.rollback()
        else:
            await db.commit()
        return report
    except Exception:
        await db.rollback()
        raise


async def _link_vendors(
    db: AsyncSession, now: datetime
) -> tuple[list[VendorLink], list[VendorLink]]:
    linked: list[VendorLink] = []
    needing: list[VendorLink] = []
    for vendor in (await db.execute(select(Vendor).order_by(Vendor.name))).scalars():
        before = vendor.brand_family_id
        match = await brands.link_vendor(db, vendor, now=now)
        if match.reason:
            needing.append(VendorLink(vendor=vendor.name, family=None, reason=match.reason))
        elif vendor.brand_family_id != before and match.family is not None:
            linked.append(
                VendorLink(
                    vendor=vendor.name, family=match.family.name, matched_by=match.matched_by
                )
            )
    await db.flush()
    return linked, needing


async def _link_products(db: AsyncSession) -> int:
    changed = 0
    rows = await db.execute(select(Product).where(Product.brand.is_not(None)))
    for product in rows.scalars():
        changed += await brands.link_product(db, product)
    await db.flush()
    return changed
