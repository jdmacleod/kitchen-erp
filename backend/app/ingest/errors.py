"""Typed failures for the ingest pipeline.

Every error carries a short machine-readable ``code`` that is safe to store in
``ingest_job.last_error`` and to log. Messages never contain receipt text.
"""

from __future__ import annotations


class IngestError(Exception):
    code = "ingest_error"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message or self.__class__.code)
        if code is not None:
            self.code = code


class RetryableError(IngestError):
    """Transient: the job waits with backoff and is not counted as failed."""

    code = "retryable"


class ModelUnavailable(RetryableError):
    """The model server could not be reached, timed out, or has no such model."""

    code = "model_unavailable"


class InvalidModelOutput(IngestError):
    """The model answered, but nothing it said validated against the schema."""

    code = "invalid_model_output"


class OcrUnavailable(IngestError):
    """An adapter cannot produce text for this document (not an error of the document)."""

    code = "ocr_unavailable"


class StageFailure(IngestError):
    """A stage cannot complete; retried with backoff, then the job is marked failed."""

    code = "stage_failure"
