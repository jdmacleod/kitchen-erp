"""Typed failures for the ingest pipeline.

Every error carries a short machine-readable ``code`` that is safe to store in
``ingest_job.last_error`` and to log, and may carry a ``detail``: a few words
naming the specific condition behind the code (``ReadTimeout after 120s``,
``application/pdf``). The code is the contract the UI maps to a sentence; the
detail is what distinguishes two occurrences of the same code. Neither ever
contains receipt text.

Retry policy is chosen by the class, not by the caller:

* :class:`RetryableError` — a condition outside this deployment that a person
  fixes. The job waits with capped backoff and is never marked failed.
* every other :class:`IngestError` — retried with backoff up to
  ``INGEST_MAX_ATTEMPTS``, then the job fails and stops.

The distinction matters: a request that will time out identically on every
attempt is not transient, and waiting for it forever means the job neither
succeeds nor fails.
"""

from __future__ import annotations


class IngestError(Exception):
    code = "ingest_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message or detail or self.__class__.code)
        if code is not None:
            self.code = code
        self.detail = detail


class RetryableError(IngestError):
    """Transient: the job waits with backoff and is not counted as failed."""

    code = "retryable"


class ModelUnavailable(RetryableError):
    """The model server could not be reached, or has no such model.

    Retried indefinitely on purpose: the server is down or the model is not
    pulled, the deployment works without one, and the job resumes when it
    appears.
    """

    code = "model_unavailable"


class ModelMissing(ModelUnavailable):
    """The server answered, and said this model cannot serve the request.

    ``reason`` is ``not_found`` for Ollama's 404 "model … not found", or
    ``not_multimodal`` for its 400 "Multimodal data provided, but model does not
    support multimodal requests". Any other non-200 answer, a proxy's bare 404
    included, stays a plain :class:`ModelUnavailable`.

    It keeps its parent's code, detail and retry policy, so the text path and
    naming wait exactly as before. Only a vision reader tells it apart, and
    falls back to text instead of waiting.
    """

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        super().__init__(detail=detail)
        self.reason = reason


class ModelTimeout(IngestError):
    """The model server was reached but did not answer within the timeout.

    Deliberately not retryable-forever. The same request against the same model
    on the same hardware will time out again; the fix is a larger
    ``LLM_TIMEOUT_SECONDS`` or a smaller model, both of which are outside the
    job. It retries a bounded number of times and then fails, so the operator
    sees a failed job rather than one that waits for ever.
    """

    code = "model_timeout"


class InvalidModelOutput(IngestError):
    """The model answered, but nothing it said validated against the schema.

    ``model_out_of_room`` (same class, another code): the reply stopped because
    the context filled, so it was not retried. ``attempts`` says how many
    requests were made when fewer than the retry limit.
    """

    code = "invalid_model_output"
    attempts: int | None = None


class OcrUnavailable(IngestError):
    """An adapter cannot produce text for this document (not an error of the document)."""

    code = "ocr_unavailable"


class StageFailure(IngestError):
    """A stage cannot complete; retried with backoff, then the job is marked failed."""

    code = "stage_failure"
