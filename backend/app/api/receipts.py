"""Receipt upload and ingest job status (Phase 2C)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response

from app.api.deps import CurrentUser, DbSession
from app.core.errors import ApiError
from app.core.idempotency import HEADER, IdempotencyGuard
from app.ingest.paths import document_path
from app.schemas.receipts import (
    ConvertToManualOut,
    IngestJobDetail,
    IngestJobList,
    IngestJobOut,
    ReceiptDocumentOut,
    ReceiptUploadOut,
    StageResultOut,
)
from app.services import ingest, receipt_images

router = APIRouter(tags=["receipts"])

# Read the multipart body in bounded chunks; the service enforces the limit too.
_CHUNK = 1024 * 1024


def _decimal_or_none(value: str | None, name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ApiError(422, "validation_error", f"{name} must be a decimal string.") from None


async def _read_upload(image: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await image.read(_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ApiError(413, "payload_too_large", "The receipt image exceeds the size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/receipts", response_model=ReceiptUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_receipt(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    image: Annotated[UploadFile, File()],
    ocr_text: Annotated[str | None, Form()] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
    lat: Annotated[str | None, Form()] = None,
    lon: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    from app.core.config import get_settings

    data = await _read_upload(image, get_settings().receipt_max_bytes)
    # The generic Idempotency dependency hashes request.body(), which the multipart
    # parser has already consumed; hash the parsed parts instead (same semantics).
    parts = (ocr_text or "", captured_at.isoformat() if captured_at else "", lat or "", lon or "")
    request_hash = hashlib.sha256(
        b"POST\n/receipts\n" + hashlib.sha256(data).digest() + "\n".join(parts).encode()
    ).hexdigest()
    guard = IdempotencyGuard(request.headers.get(HEADER), request_hash, user, db)
    await guard.load()
    if guard.replay is not None:
        return guard.replay
    result = await ingest.upload_receipt(
        db,
        user=user,
        data=data,
        ocr_text=ocr_text,
        captured_at=captured_at,
        lat=_decimal_or_none(lat, "lat"),
        lon=_decimal_or_none(lon, "lon"),
    )
    body = ReceiptUploadOut(
        document=ReceiptDocumentOut.from_model(result.document),
        job=IngestJobOut.model_validate(result.job),
    ).model_dump(mode="json")
    return await guard.commit(201 if result.created else 200, body)


@router.get("/receipts/{document_id}", response_model=ReceiptDocumentOut)
async def get_receipt(document_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ReceiptDocumentOut:
    return ReceiptDocumentOut.from_model(await ingest.get_document(db, document_id))


_IMAGE_HEADERS = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}


@router.get("/receipts/{document_id}/image", response_model=None)
async def get_receipt_image(
    document_id: uuid.UUID,
    _: CurrentUser,
    db: DbSession,
    width: Annotated[
        int | None, Query(ge=receipt_images.THUMB_MIN, le=receipt_images.THUMB_MAX)
    ] = None,
) -> FileResponse | Response:
    """The receipt as a browser can show it: the original, or page 1 as PNG (#30).

    `width` asks for a PNG no wider than that, for a thumbnail in a list (#28).
    """
    document = await ingest.get_document(db, document_id)
    path = document_path(document)
    if not path.is_file():
        raise ApiError(404, "image_missing", "The stored image is missing.")
    shown = await receipt_images.displayable(path, document.mime, width)
    if shown.path is not None:
        return FileResponse(shown.path, media_type=shown.media_type, headers=_IMAGE_HEADERS)
    return Response(content=shown.content, media_type=shown.media_type, headers=_IMAGE_HEADERS)


@router.get("/ingest-jobs", response_model=IngestJobList)
async def list_ingest_jobs(
    _: CurrentUser,
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    stage: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> IngestJobList:
    cursor_id = None
    if cursor:
        try:
            cursor_id = uuid.UUID(cursor)
        except ValueError:
            raise ApiError(422, "validation_error", "Invalid cursor.") from None
    jobs, next_cursor = await ingest.list_jobs(
        db, status=status_filter, stage=stage, cursor=cursor_id, limit=limit
    )
    return IngestJobList(
        items=[IngestJobOut.model_validate(j) for j in jobs],
        next_cursor=None if next_cursor is None else str(next_cursor),
    )


@router.get("/ingest-jobs/{job_id}", response_model=IngestJobDetail)
async def get_ingest_job(
    job_id: uuid.UUID,
    _: CurrentUser,
    db: DbSession,
    include_output: Annotated[bool, Query()] = False,
) -> IngestJobDetail:
    job = await ingest.get_job(db, job_id)
    results = await ingest.stage_results_for(db, job.id)
    detail = IngestJobDetail.model_validate(job)
    detail.stage_results = [
        StageResultOut(
            id=r.id,
            stage=r.stage,
            adapter=r.adapter,
            adapter_version=r.adapter_version,
            duration_ms=r.duration_ms,
            created_at=r.created_at,
            output=r.output if include_output else None,
        )
        for r in results
    ]
    return detail


@router.post("/ingest-jobs/{job_id}/retry", response_model=IngestJobOut)
async def retry_ingest_job(job_id: uuid.UUID, _: CurrentUser, db: DbSession) -> IngestJobOut:
    return IngestJobOut.model_validate(await ingest.retry_job(db, job_id))


@router.post("/ingest-jobs/{job_id}/to-manual", response_model=ConvertToManualOut)
async def convert_ingest_job_to_manual(
    job_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ConvertToManualOut:
    job, purchase = await ingest.convert_to_manual(db, job_id, user)
    return ConvertToManualOut(job=IngestJobOut.model_validate(job), purchase_id=purchase.id)
