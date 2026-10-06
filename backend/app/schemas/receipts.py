"""Request and response models for receipt upload and ingest jobs (Phase 2C)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.base import ApiModel, DecimalStr


class ReceiptDocumentOut(ApiModel):
    id: uuid.UUID
    sha256: str
    mime: str
    bytes: int
    captured_at: datetime | None
    has_capture_geo: bool
    has_client_ocr_text: bool
    uploaded_by: uuid.UUID
    created_at: datetime

    @classmethod
    def from_model(cls, document: Any) -> ReceiptDocumentOut:
        return cls(
            id=document.id,
            sha256=document.sha256,
            mime=document.mime,
            bytes=document.bytes,
            captured_at=document.captured_at,
            has_capture_geo=document.capture_geo is not None,
            has_client_ocr_text=document.client_ocr_text is not None,
            uploaded_by=document.uploaded_by,
            created_at=document.created_at,
        )


class IngestJobOut(ApiModel):
    id: uuid.UUID
    receipt_document_id: uuid.UUID
    stage: str
    status: str
    attempts: int
    last_error: str | None
    last_error_detail: str | None = None
    next_attempt_at: datetime | None
    locked_at: datetime | None
    purchase_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # When it was last sent to be read: a receipt removed and uploaded again
    # shows that day, not its first upload (issue 122).
    uploaded_at: datetime


class StageResultOut(ApiModel):
    id: uuid.UUID
    stage: str
    adapter: str
    adapter_version: str
    duration_ms: int
    created_at: datetime
    output: dict[str, Any] | None = None


class IngestJobDetail(IngestJobOut):
    stage_results: list[StageResultOut] = Field(default_factory=list)


class IngestJobList(ApiModel):
    items: list[IngestJobOut]
    next_cursor: str | None = None


class ReceiptUploadOut(ApiModel):
    document: ReceiptDocumentOut
    job: IngestJobOut
    # This receipt was removed before and is being read again (#74).
    revived: bool = False
    # The upload batch it was counted in (issue 122).
    batch_id: uuid.UUID


class UploadBatchCreate(ApiModel):
    # How many files the person chose, before any is sent.
    file_count: int = Field(ge=1, le=500)


class UploadBatchOut(ApiModel):
    id: uuid.UUID
    file_count: int
    created_at: datetime


class BatchReceiptOut(ApiModel):
    """One file in a batch, with what its reading came to once read."""

    job: IngestJobOut
    outcome: Literal["new", "revived", "already_seen"]
    store: str | None = None
    total: DecimalStr | None = None
    item_lines: int | None = None
    lines_total: DecimalStr | None = None
    # Null while it is being read, or when it was entered by hand instead.
    trust: Literal["adds_up", "check_lines", "couldnt_read"] | None = None
    # With check_lines: how far the lines are from the printed total.
    gap: DecimalStr | None = None
    held: bool = False


class UploadBatchSummaryOut(UploadBatchOut):
    """A batch with its counts: what was uploaded, and what the readings came to."""

    uploaded: int
    new: int
    revived: int
    already_seen: int
    reading: int
    adds_up: int
    check_lines: int
    couldnt_read: int
    receipts: list[BatchReceiptOut]


class UploadBatchList(ApiModel):
    items: list[UploadBatchSummaryOut]
    next_cursor: str | None = None


class ReceiptRemovedOut(ApiModel):
    photo_deleted: bool


class ConvertToManualOut(ApiModel):
    job: IngestJobOut
    purchase_id: uuid.UUID
