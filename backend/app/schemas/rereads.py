"""Re-reading a receipt's lines (04, 2S): request, proposal and its outcome."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import model_validator

from app.schemas.base import ApiModel

RereadMode = Literal["check", "read"]
RereadStatus = Literal[
    "pending", "running", "ready", "no_changes", "failed", "applied", "dismissed"
]


class RereadRequest(ApiModel):
    mode: RereadMode


class RereadLine(ApiModel):
    """A line's values as the page shows them; amounts are decimal strings."""

    raw_text: str | None
    line_kind: str
    qty: str | None
    unit: str | None
    unit_price: str | None
    line_total: str
    attached: str | None  # the receipt text of the item a discount or deposit belongs to
    flags: list[str]


class RereadChange(ApiModel):
    action: Literal["changed", "added", "removed", "kept"]
    line_id: uuid.UUID | None = None
    before: RereadLine | None = None
    after: RereadLine | None = None
    fields: list[str] | None = None  # changed: the fields that move
    why: Literal["edited", "added", "removed"] | None = None  # kept: what the person did
    reading: RereadLine | None = None  # kept, removed by a person: what the reading has


class Reconciliation(ApiModel):
    checked: bool
    items: str
    discounts: str
    tax: str
    tax_source: str
    deposits: str
    fees: str
    computed_total: str
    printed_total: str | None
    difference: str | None
    mismatch: bool


class RereadSide(ApiModel):
    reconciliation: Reconciliation
    flags: list[str]


class RereadCounts(ApiModel):
    changed: int
    added: int
    removed: int
    kept: int


class RereadProposal(ApiModel):
    changes: list[RereadChange]
    counts: RereadCounts
    before: RereadSide
    after: RereadSide


class RereadOut(ApiModel):
    id: uuid.UUID
    purchase_id: uuid.UUID
    mode: RereadMode
    status: RereadStatus
    reason: str | None
    proposal: RereadProposal | None
    created_at: datetime
    decided_at: datetime | None

    @model_validator(mode="before")
    @classmethod
    def _public_proposal(cls, data: Any) -> Any:
        """Only what the page shows: the rows to apply and the raw answer stay inside."""
        if not isinstance(data, dict):
            data = {name: getattr(data, name) for name in cls.model_fields}
        proposal = data.get("proposal")
        if isinstance(proposal, dict):
            data = {
                **data,
                "proposal": {k: proposal[k] for k in ("changes", "counts", "before", "after")},
            }
        return data
