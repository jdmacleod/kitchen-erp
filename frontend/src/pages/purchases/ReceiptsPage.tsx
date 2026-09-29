import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import { jobInFlight, useIngestJob, useIngestJobs, useJobToManual, useRemoveJob, useRetryJob, useUploadReceipt, type IngestJob } from "../../api/ingest";
import { Badge } from "../../components/catalog/fields";
import { ReceiptImage } from "../../components/purchases/ReceiptImage";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { ingestErrorText } from "../../lib/ingestErrors";
import { usePageTitle } from "../../lib/usePageTitle";
import { useNotice } from "../../components/Notice";

/** Upload a receipt photo and watch its job; a finished job links to its review. */
export function ReceiptsPage() {
  usePageTitle("Receipts");
  const upload = useUploadReceipt();
  const notice = useNotice();
  const jobs = useIngestJobs();
  // An inbox item names its job (?job=), which may be older than the newest jobs listed.
  const [params, setParams] = useSearchParams();
  const pinnedId = params.get("job") ?? undefined;
  const pinned = useIngestJob(pinnedId, false);
  // Listed once: the list drops the job only while the pinned card is showing it.
  const listed = (jobs.data ?? []).filter((j) => !(pinned.data && j.id === pinned.data.id));
  const fileRef = useRef<HTMLInputElement>(null);
  const [invalid, setInvalid] = useState<string | null>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return setInvalid("Choose a photo of the receipt.");
    setInvalid(null);
    upload.mutate(
      { image: file },
      {
        onSuccess: (result) => {
          if (fileRef.current) fileRef.current.value = "";
          if (result.revived) notice.show({ tone: "info", message: "This receipt was removed before; it's being read again." });
          else if (result.created === false)
            // Nothing new is read: saying "once it's read" sent people waiting for nothing.
            notice.show({
              tone: "info",
              message: "You've uploaded this receipt before, so it isn't read again.",
              action: result.job.purchase_id ? { label: "Open its purchase", to: `/shop/purchases/${result.job.purchase_id}` } : undefined,
            });
          else notice.show({ tone: "success", message: "Receipt uploaded. It'll appear in Needs you once it's read." });
        },
      },
    );
  };

  return (
    <>
      <PageHeader title="Receipts" description="Upload a photo and its lines are read for you to review." />
      <div className="flex flex-col gap-6">
        <Card>
          <form onSubmit={submit} aria-labelledby="upload-heading" className="flex flex-col gap-3" noValidate>
            <h2 id="upload-heading" className="text-lg font-medium">
              Upload a receipt
            </h2>
            {invalid ? <Alert tone="error">{invalid}</Alert> : null}
            {upload.error ? <Alert tone="error">{errorMessage(upload.error)}</Alert> : null}
            <div className="flex flex-col gap-1">
              <label htmlFor="receipt-image" className="text-sm font-medium">
                Photo
              </label>
              {/* The server's allowlist (app/ingest/formats.py) is the one that
                  decides; this only filters the picker, and image/* alone used to
                  hide the PDFs that emailed receipts arrive as. */}
              <input id="receipt-image" ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp,image/heic,application/pdf" capture="environment" className={`min-h-11 text-sm lg:min-h-0 ${focusRing}`} />
              <p className="text-xs text-neutral-600 dark:text-neutral-400">A photo or a PDF. It stays on this deployment; nothing is sent elsewhere.</p>
            </div>
            <div>
              <Button type="submit" disabled={upload.isPending}>
                {upload.isPending ? "Uploading…" : "Upload"}
              </Button>
            </div>
          </form>
        </Card>

        {pinnedId ? (
          <Card>
            <h2 className="mb-3 text-lg font-medium">From your inbox</h2>
            {pinned.isPending ? (
              <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
                Loading…
              </p>
            ) : pinned.isError ? (
              isApiError(pinned.error) && pinned.error.code === "receipt_removed" ? (
                <p className="text-sm text-neutral-600 dark:text-neutral-400">This receipt was removed.</p>
              ) : (
                <Alert tone="error">{errorMessage(pinned.error)}</Alert>
              )
            ) : (
              // Removed from here, it has nothing left to show: drop the pin.
              <JobRow job={pinned.data} onRemoved={() => setParams({}, { replace: true })} />
            )}
          </Card>
        ) : null}

        <Card>
          <h2 className="mb-3 text-lg font-medium">Jobs</h2>
          {jobs.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading…
            </p>
          ) : jobs.isError ? (
            <Alert tone="error">{errorMessage(jobs.error)}</Alert>
          ) : listed.length === 0 && !pinnedId ? (
            <EmptyState title="No receipts yet">Upload a photo and its lines will be parsed for review.</EmptyState>
          ) : (
            <ul aria-label="Ingest jobs" className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {listed.map((j) => (
                <li key={j.id} className="py-2">
                  <JobRow job={j} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

const statusText: Record<string, string> = {
  pending: "queued",
  running: "running",
  needs_review: "ready to review",
  done: "done",
  failed: "failed",
};

/**
 * A pending job that has already failed an attempt is retrying, not queued.
 * Reporting it as "queued" was how a job could sit in a failing retry loop for
 * minutes while the page implied nothing had happened yet (issue #13).
 */
function jobStatusText(job: IngestJob): string {
  if (job.status === "pending" && (job.attempts ?? 0) > 0) return "retrying";
  return statusText[job.status] ?? job.status;
}

function JobRow({ job, onRemoved }: { job: IngestJob; onRemoved?: () => void }) {
  const retry = useRetryJob();
  const toManual = useJobToManual();
  const remove = useRemoveJob();
  // A failed read with no purchase has nowhere else to be removed from (D5);
  // one that has a draft is removed from the purchase page.
  const removable = job.status === "failed" && !job.purchase_id;
  const [confirming, setConfirming] = useState(false);
  const keep = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) keep.current?.focus();
  }, [confirming]);
  const tone = job.status === "done" ? "good" : job.status === "failed" ? "danger" : job.last_error ? "warn" : "neutral";
  const error = job.last_error ? ingestErrorText(job.last_error, job.last_error_detail) : null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm" data-testid="ingest-job" data-status={job.status}>
      {/* Which receipt this is (#28): two failed jobs otherwise read the same. */}
      {job.receipt_document_id ? (
        <ReceiptImage
          documentId={job.receipt_document_id}
          width={96}
          alt=""
          link={{ label: `Open the receipt uploaded ${job.created_at ? formatDateTime(job.created_at) : ""}`.trim() }}
          className="h-16 w-12 rounded border border-neutral-200 bg-white object-cover object-top dark:border-neutral-800"
        />
      ) : null}
      <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        <Badge tone={tone}>{jobStatusText(job)}</Badge>
        {job.stage && jobInFlight(job) ? <span className="text-neutral-600 dark:text-neutral-400">stage {job.stage}</span> : null}
        {job.created_at ? <time dateTime={job.created_at}>{formatDateTime(job.created_at)}</time> : null}
        {error ? (
          <span className="text-red-700 dark:text-red-300">
            {error.message}{" "}
            {/* The code and the condition stay visible: the sentence is for acting
                on, these two are what a log search or a bug report needs. */}
            <span className="text-xs text-neutral-600 dark:text-neutral-400">
              ({error.detail ? `${error.code}: ${error.detail}` : error.code})
            </span>
          </span>
        ) : null}
      </span>
      <span className="flex flex-wrap gap-1">
        {job.purchase_id ? (
          <Link to={`/shop/purchases/${job.purchase_id}`} className={`inline-flex min-h-11 lg:min-h-8 items-center rounded-md px-2 text-sm font-medium underline ${focusRing}`}>
            {job.status === "done" ? "View purchase" : "Review purchase"}
          </Link>
        ) : null}
        {job.status === "failed" ? (
          <Button variant="secondary" className="min-h-11 lg:min-h-8 px-2 text-xs" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>
            Retry
          </Button>
        ) : null}
        {job.status === "failed" || job.status === "pending" ? (
          <Button variant="secondary" className="min-h-11 lg:min-h-8 px-2 text-xs" disabled={toManual.isPending} onClick={() => toManual.mutate(job.id)}>
            Enter by hand
          </Button>
        ) : null}
        {removable && !confirming ? (
          <Button ref={trigger} variant="danger" className="min-h-11 lg:min-h-8 px-2 text-xs" onClick={() => setConfirming(true)}>
            Remove
          </Button>
        ) : null}
      </span>
      {removable && confirming ? (
        <div role="group" aria-label="Remove this receipt" className="flex w-full flex-wrap items-center gap-2 rounded-md border border-red-300 bg-red-50 p-2 dark:border-red-900 dark:bg-red-950">
          <span className="text-sm">Remove this receipt? Its photo is deleted.</span>
          {remove.isError ? <span role="alert" className="text-sm text-red-700 dark:text-red-300">{errorMessage(remove.error)}</span> : null}
          <Button variant="dangerFill" className="min-h-11 lg:min-h-8 px-2 text-xs" disabled={remove.isPending} onClick={() => remove.mutate(job.id, { onSuccess: onRemoved })}>
            {remove.isPending ? "Removing…" : "Remove"}
          </Button>
          <Button
            ref={keep}
            variant="secondary"
            className="min-h-11 lg:min-h-8 px-2 text-xs"
            disabled={remove.isPending}
            onClick={() => {
              setConfirming(false);
              remove.reset();
              requestAnimationFrame(() => trigger.current?.focus());
            }}
          >
            Keep it
          </Button>
        </div>
      ) : null}
    </div>
  );
}
