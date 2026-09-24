"""Request and response models for receipt upload and ingest jobs (Phase 2C)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.base import ApiModel


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


class ConvertToManualOut(ApiModel):
    job: IngestJobOut
    purchase_id: uuid.UUID
