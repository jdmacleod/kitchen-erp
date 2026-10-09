"""Criterion 99 (03, 1I): the stock-photo mark behind ``select_primary``'s ranking.

A vendor-page photo that three or more of the same vendor's other products also carry is a
likely stock photo. Invented vendors and products only.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProductImage
from app.services.proposals import STOCK_PHOTO_PRODUCTS, _stock_check
from tests.pricebook_helpers import make_location, make_product

SHARED_PHASH = 0x5A5A_5A5A_5A5A_5A5A


def page_photo(
    product_id: str,
    vendor_id: uuid.UUID | None,
    sha: str,
    *,
    phash: int | None = SHARED_PHASH,
    source_kind: str = "vendor_listing",
) -> ProductImage:
    return ProductImage(
        product_id=uuid.UUID(product_id),
        vendor_id=vendor_id,
        upload_sha256=sha * 64,
        sha256=sha * 64,
        source_kind=source_kind,
        status="active",
        width=800,
        height=800,
        phash=phash,
    )


async def _vendor_with_products(client, db: AsyncSession, name: str, count: int, sha0: int):
    """A vendor whose ``count`` products each carry the shared page photo."""
    loc = await make_location(client, name, name)
    vendor_id = uuid.UUID(loc["vendor"]["id"])
    for i in range(count):
        other = await make_product(client, f"{name} grain {i}", f"{name} grain {i}")
        db.add(page_photo(other["id"], vendor_id, chr(sha0 + i)))
    await db.flush()
    return vendor_id


CASES = [
    ("no other product has it", 0, False),
    ("one short of the bar", STOCK_PHOTO_PRODUCTS - 1, False),
    ("exactly at the bar", STOCK_PHOTO_PRODUCTS, True),
    ("past the bar", STOCK_PHOTO_PRODUCTS + 1, True),
]


@pytest.mark.parametrize("label, others, expected", CASES, ids=[c[0] for c in CASES])
async def test_a_page_photo_shared_by_three_other_products_is_marked_stock(
    admin_client, db_session: AsyncSession, label: str, others: int, expected: bool
):
    vendor_id = await _vendor_with_products(
        admin_client, db_session, "Hollowbeck Market", others, ord("b")
    )
    checked = await make_product(admin_client, "Lentils", "Hollowbeck lentils")
    image = page_photo(checked["id"], vendor_id, "a")
    db_session.add(image)
    await db_session.flush()

    await _stock_check(db_session, image)

    assert image.is_stock_suspect is expected, label


async def test_only_the_same_vendors_page_photos_count(admin_client, db_session: AsyncSession):
    # Three products at another vendor carry the same photo: that says nothing about this one.
    await _vendor_with_products(
        admin_client, db_session, "Quillmoor Grocer", STOCK_PHOTO_PRODUCTS, ord("b")
    )
    vendor_id = await _vendor_with_products(
        admin_client, db_session, "Hollowbeck Market", 0, ord("b")
    )
    checked = await make_product(admin_client, "Lentils", "Hollowbeck lentils")
    image = page_photo(checked["id"], vendor_id, "a")
    db_session.add(image)
    await db_session.flush()
    await _stock_check(db_session, image)
    assert image.is_stock_suspect is False

    # One other product carrying the photo three times is one product, not three.
    repeat = await make_product(admin_client, "Barley", "Hollowbeck barley")
    for sha in ("x", "y", "z"):
        db_session.add(page_photo(repeat["id"], vendor_id, sha))
    await db_session.flush()
    await _stock_check(db_session, image)
    assert image.is_stock_suspect is False


async def test_a_persons_own_photo_is_never_checked(admin_client, db_session: AsyncSession):
    vendor_id = await _vendor_with_products(
        admin_client, db_session, "Hollowbeck Market", STOCK_PHOTO_PRODUCTS, ord("b")
    )
    checked = await make_product(admin_client, "Lentils", "Hollowbeck lentils")
    own = page_photo(checked["id"], vendor_id, "a", source_kind="user_photo")
    without_hash = page_photo(checked["id"], vendor_id, "w", phash=None)
    db_session.add_all([own, without_hash])
    await db_session.flush()

    await _stock_check(db_session, own)
    await _stock_check(db_session, without_hash)

    assert own.is_stock_suspect is False and without_hash.is_stock_suspect is False
