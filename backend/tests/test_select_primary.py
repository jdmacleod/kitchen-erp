"""Criterion 99: the main photo rule, as a table of photo sets (03, 1I)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.catalog.photos import PhotoFacts, select_primary

T0 = datetime(2026, 5, 1, tzinfo=UTC)


def photo(name: str, source: str = "user_photo", **kw) -> PhotoFacts:
    defaults = {
        "role": "product",
        "status": "active",
        "created_at": T0,
        "width": 1000,
        "height": 1000,
    }
    return PhotoFacts(id=uuid.uuid5(uuid.NAMESPACE_DNS, name), source_kind=source, **defaults | kw)


_NAMES: dict[uuid.UUID, str] = {}


def named(name: str, source: str = "user_photo", **kw) -> PhotoFacts:
    p = photo(name, source, **kw)
    _NAMES[p.id] = name
    return p


CASES = [
    ("nothing to choose", [], None),
    (
        "own photo with a cutout beats one without",
        [named("plain"), named("cut", has_cutout=True)],
        "cut",
    ),
    (
        "own photo beats a manufacturer's",
        [named("maker", "manufacturer", width=3000, height=3000), named("own")],
        "own",
    ),
    (
        "manufacturer and Open Food Facts rank together, by resolution",
        [
            named("off", "open_food_facts", width=800, height=800),
            named("maker", "manufacturer", width=1200, height=1200),
        ],
        "maker",
    ),
    (
        "a manufacturer's photo beats a vendor page's",
        [named("page", "vendor_listing", width=4000, height=4000), named("maker2", "manufacturer")],
        "maker2",
    ),
    (
        "a likely stock photo ranks after everything",
        [
            named("stock", "user_photo", is_stock_suspect=True, has_cutout=True),
            named("page2", "vendor_listing"),
        ],
        "page2",
    ),
    (
        "within a rank, more pixels first",
        [named("small", width=400, height=400), named("big", width=2000, height=1500)],
        "big",
    ),
    ("then the newest", [named("old"), named("new", created_at=T0 + timedelta(days=1))], "new"),
    (
        "the person's choice wins over the rule",
        [
            named("ruled", has_cutout=True),
            named("chosen", "vendor_listing", pinned=True, pinned_at=T0),
        ],
        "chosen",
    ),
    (
        "a label photo is never the main photo",
        [named("label", role="label_front", has_cutout=True), named("page3", "vendor_listing")],
        "page3",
    ),
    ("only labels: no main photo", [named("label2", role="label_nutrition")], None),
    (
        "hidden, failed and processing photos are not shown",
        [
            named("hidden", status="hidden", has_cutout=True),
            named("failed", status="failed"),
            named("processing", status="processing"),
            named("shown", "vendor_listing"),
        ],
        "shown",
    ),
    (
        "a hidden choice no longer wins",
        [named("was-chosen", status="hidden", pinned=True, pinned_at=T0), named("rule")],
        "rule",
    ),
]


@pytest.mark.parametrize(("case", "photos", "expected"), CASES, ids=[c[0] for c in CASES])
def test_select_primary(case: str, photos: list[PhotoFacts], expected: str | None):
    chosen = select_primary(photos)
    assert (_NAMES[chosen] if chosen else None) == expected


def test_the_choice_does_not_depend_on_order():
    photos = [named(f"p{i}", width=500 + i, height=500) for i in range(5)]
    assert select_primary(photos) == select_primary(list(reversed(photos)))
