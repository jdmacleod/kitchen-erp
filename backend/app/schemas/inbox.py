from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.base import ApiModel

InboxKind = Literal[
    "receipt",
    "receipt_failed",
    "identify",
    "bridge",
    "vendor_suggestions",
    "link",
    "usda",
    "new_product",
    "product_update",
    "posted_prices",
]


class InboxItem(ApiModel):
    kind: InboxKind
    title: str
    detail: str
    action_label: str
    action_route: str
    created_at: datetime
    # A failed read's ingest error code, so the client can show the sentence it
    # already keeps for that code (frontend lib/ingestErrors.ts) instead of a copy.
    error_code: str | None = None


class InboxReading(ApiModel):
    """What is still being read: shown as a line above the inbox, not as items.

    ``count`` is receipts; ``photos`` and ``pages`` are product captures being
    identified (2L). ``oldest_at`` and ``stalled`` cover all of them.
    """

    count: int
    photos: int = 0
    pages: int = 0
    oldest_at: datetime | None = None
    # Barcodes and pages waiting on the products helper, only once overdue (PD7).
    lookups_overdue: int = 0
    lookups_since: datetime | None = None
    # True once the oldest has been in flight longer than INGEST_STALL_MINUTES, so a
    # stopped worker is not mistaken for a busy one (D21).
    stalled: bool = False


class InboxOut(ApiModel):
    items: list[InboxItem]
    reading: InboxReading
