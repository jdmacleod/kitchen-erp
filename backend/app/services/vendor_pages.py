"""A page's vendor, its listing and its posted price (04, 2M), shared by page captures and
the lookup helper's answers. No request is made: everything here reads the address
and the household's own vendors.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import proposals as merging
from app.catalog.listings import canonical_url
from app.models.geo import Vendor


def page_host(url: str | None) -> str | None:
    try:
        host = urlsplit(url or "").hostname
    except ValueError:
        return None
    return host.removeprefix("www.") if host else None


async def listing_for_page(
    db: AsyncSession, page_url: str, fields: dict[str, Any]
) -> dict[str, Any] | None:
    """The listing a page would give, when it is one of the household's vendors' pages."""
    vendor = await match_vendor(db, page_url)
    if vendor is None:
        return None
    canonical, store_ref = canonical_url(page_url)
    return {
        "vendor_id": str(vendor.id),
        "canonical_url": canonical,
        "title": merging.value(fields, "title") or canonical,
        "vendor_sku": merging.value(fields, "item_number"),
        "store_ref": store_ref,
    }


async def match_vendor(db: AsyncSession, page_url: str) -> Vendor | None:
    """The household's vendor whose website is on the page's host, if one is."""
    host = page_host(page_url)
    if not host:
        return None
    for vendor in (await db.execute(select(Vendor).where(Vendor.website.is_not(None)))).scalars():
        site = page_host(
            vendor.website if "//" in (vendor.website or "") else f"//{vendor.website}"
        )
        if site and (host == site or host.endswith(f".{site}")):
            return vendor
    return None


def posted_price(fields: dict[str, Any]) -> dict[str, Any] | None:
    """The posted price with what it is for: 1 each, or the basis the page gave."""
    basis = merging.price_basis(merging.value(fields, "price"))
    if basis is None:
        return None
    return {**basis, "is_promo": bool(merging.value(fields, "on_sale"))}
