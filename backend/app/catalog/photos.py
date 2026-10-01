"""Which photo is a product's main photo (03, 1I). Pure: no database, no I/O.

The person's choice wins. Otherwise the household's own photo with a cutout
comes first, then one without, then a manufacturer's or Open Food Facts photo,
then a vendor page's. A vendor-page photo marked as a likely stock photo ranks
after all of them. Within a rank, more pixels first, then the newest.

Only ``active`` photos in the ``product`` role can be the main photo: a label
photo never is, and a hidden, failed or still-processing one is not shown.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

SOURCE_RANK = {"manufacturer": 2, "open_food_facts": 2, "vendor_listing": 3}
STOCK_RANK = 4


@dataclass(frozen=True)
class PhotoFacts:
    """What the choice reads from a ``product_image`` row."""

    id: uuid.UUID
    source_kind: str
    role: str
    status: str
    created_at: datetime
    width: int | None = None
    height: int | None = None
    has_cutout: bool = False
    is_stock_suspect: bool = False
    pinned: bool = False
    pinned_at: datetime | None = None


def eligible(photo: PhotoFacts) -> bool:
    return photo.role == "product" and photo.status == "active"


def rank(photo: PhotoFacts) -> int:
    if photo.is_stock_suspect:
        return STOCK_RANK
    if photo.source_kind == "user_photo":
        return 0 if photo.has_cutout else 1
    return SOURCE_RANK[photo.source_kind]


def select_primary(photos: Iterable[PhotoFacts]) -> uuid.UUID | None:
    """The id of the main photo among ``photos``, or None when none can be."""
    candidates = [p for p in photos if eligible(p)]
    if not candidates:
        return None
    pinned = [p for p in candidates if p.pinned]
    if pinned:
        return max(pinned, key=lambda p: (p.pinned_at or p.created_at, p.id)).id
    best = min(
        candidates,
        key=lambda p: (
            rank(p),
            -((p.width or 0) * (p.height or 0)),
            -p.created_at.timestamp(),
            p.id,
        ),
    )
    return best.id
