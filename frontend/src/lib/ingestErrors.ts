/**
 * What an ingest error code means, in words the person running this deployment
 * can act on.
 *
 * The Receipts page used to render `job.last_error` raw, so a model that had
 * simply been given too little time said `model_unavailable` and sent the owner
 * to check a model server that was working (issue #14), and a receipt in a
 * format nothing could read said `no_ocr_text` without mentioning the format
 * (issue #13). The codes are a closed set the backend defines in
 * `app/ingest/errors.py`; each one gets a sentence here, and the code itself
 * stays on screen beside it so a support question can still quote it.
 *
 * A code with no entry falls back to the code. That is the old behaviour, and
 * it is the right default: a new code showing through unprosed is a visible
 * prompt to add it, where a generic "something went wrong" would hide it.
 */
const MESSAGES: Record<string, string> = {
  // Model server
  model_timeout: "The model server answered too slowly. Raise LLM_TIMEOUT_SECONDS, or use a smaller model.",
  model_unavailable: "The model server could not be reached. Check that Ollama is running and that the model is pulled.",
  invalid_model_output: "The model's answer did not fit the expected shape. Retry, or enter this receipt by hand.",

  // Reading the document
  no_ocr_text: "No text could be read from this receipt.",
  pdf_unreadable: "This PDF could not be opened. It may be damaged or password-protected.",
  pdf_too_large: "This PDF's first page is too large to render.",
  heif_unreadable: "This HEIC photo could not be decoded.",
  heif_too_large: "This photo is too large to process.",
  tesseract_failed: "The text recogniser could not read this image.",
  tesseract_timeout: "The text recogniser took too long on this image.",
  tesseract_spawn_failed: "The text recogniser could not be started. Check the worker image.",

  // The job's own inputs
  image_missing: "The stored image for this receipt is missing from disk.",
  document_missing: "The receipt this job was created for no longer exists.",
  no_purchase: "This job has no draft purchase to resolve.",
  unexpected_error: "Something failed that this deployment does not have a message for. The worker log has the details.",
};

export type IngestErrorText = { message: string; code: string; detail: string | null };

/** The sentence for a code, the code itself, and the condition behind it. */
export function ingestErrorText(code: string, detail?: string | null): IngestErrorText {
  return { message: MESSAGES[code] ?? code, code, detail: detail ?? null };
}
