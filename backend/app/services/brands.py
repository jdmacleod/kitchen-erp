"""Which brand a product names, and whose stores a vendor's are (spec 16, 2R).

Brands and families come from ``kerp import brands``. A product links to the
brand its printed ``brand`` names, by name key, exactly or by its longest
leading run of words that is an alias; only brand text is looked at, never a
title. A vendor links to its family by Wikidata id, then website domain, then
an unambiguous name. Both links are written as a source would write them
(``write_unless_edited``), so a person's choice on a vendor is kept.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.brand_names import leading_keys, name_key
from app.models.brands import Brand, BrandAlias, BrandFamily, BrandFamilyBanner
from app.models.catalog import Product
from app.models.geo import Vendor
from app.services.interchange import write_unless_edited

SOURCE = "kitchen-erp-brands"
# No alias in the dataset runs longer than this many words.
LONGEST_ALIAS = 8

MatchedBy = Literal["wikidata", "domain", "name"]


async def find_brand(db: AsyncSession, text: str | None) -> Brand | None:
    """The brand `text` names, or None for a national or unknown brand."""
    if not text or not (key := name_key(text)):
        return None
    keys = [key, *leading_keys(text, LONGEST_ALIAS)]
    rows = (
        (await db.execute(select(BrandAlias).where(BrandAlias.alias_key.in_(keys)))).scalars().all()
    )
    if not rows:
        return None
    best = max(rows, key=lambda a: len(a.alias_key.split()))
    # Loaded whole, with its family and replacement, so a product's response can
    # show them without another round trip.
    return (
        await db.execute(
            select(Brand).where(Brand.id == best.brand_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def link_product(db: AsyncSession, product: Product) -> bool:
    """Point `product.brand_id` at the brand its text names. True if it changed."""
    brand = await find_brand(db, product.brand)
    unchanged = product.brand_id == (brand.id if brand else None)
    product.house_brand = brand  # sets brand_id on flush, and the loaded row now
    return not unchanged


async def is_house_brand(db: AsyncSession, text: str | None) -> bool:
    """Every brand the dataset lists is some family's own or a wholesale label."""
    return await find_brand(db, text) is not None


# --- vendors -------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyMatch:
    family: BrandFamily | None
    matched_by: MatchedBy | None
    reason: str | None = None  # why nothing matched, when a person should choose


def _host(url: str | None) -> str:
    if not url:
        return ""
    if "//" not in url:
        url = f"https://{url}"
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _on_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


async def family_for_vendor(db: AsyncSession, vendor: Vendor) -> FamilyMatch:
    """The first rule that gives exactly one family: Wikidata id, website domain, name."""
    banners = (await db.execute(select(BrandFamilyBanner))).scalars().all()
    families = {f.id: f for f in (await db.execute(select(BrandFamily))).scalars().all()}

    def one(ids: set[uuid.UUID], by: MatchedBy) -> FamilyMatch | None:
        if len(ids) == 1:
            return FamilyMatch(families[next(iter(ids))], by)
        if len(ids) > 1:
            names = sorted(families[i].name for i in ids)
            return FamilyMatch(None, None, f"its {by} fits {', '.join(names)}")
        return None

    if vendor.wikidata:
        ids = {b.family_id for b in banners if b.wikidata == vendor.wikidata}
        ids |= {f.id for f in families.values() if f.wikidata == vendor.wikidata}
        if found := one(ids, "wikidata"):
            return found
    if host := _host(vendor.website):
        ids = {b.family_id for b in banners if any(_on_domain(host, d) for d in b.domains)}
        if found := one(ids, "domain"):
            return found
    keys = {name_key(t) for t in (vendor.name, vendor.brand) if t} - {""}
    ids = {b.family_id for b in banners if name_key(b.name) in keys}
    return one(ids, "name") or FamilyMatch(None, None)


async def link_vendor(
    db: AsyncSession, vendor: Vendor, *, now: datetime | None = None
) -> FamilyMatch:
    """Set `vendor.brand_family_id` from `family_for_vendor`, unless a person chose it."""
    match = await family_for_vendor(db, vendor)
    if match.reason is None:
        write_unless_edited(
            vendor,
            "brand_family_id",
            match.family.id if match.family else None,
            source=SOURCE,
            ref=match.matched_by,
            now=now or datetime.now(UTC),
            stored=str,
        )
    return match
