from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.base import ApiModel

InboxKind = Literal["receipt", "receipt_failed", "identify", "bridge"]


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
    """Receipts still being read: shown as a line above the inbox, not as items."""

    count: int
    oldest_at: datetime | None = None
    # True once the oldest has been in flight longer than INGEST_STALL_MINUTES, so a
    # stopped worker is not mistaken for a busy one (D21).
    stalled: bool = False


class InboxOut(ApiModel):
    items: list[InboxItem]
    reading: InboxReading
