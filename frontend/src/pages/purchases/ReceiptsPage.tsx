import { useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { jobInFlight, useIngestJobs, useJobToManual, useRetryJob, useUploadReceipt, type IngestJob } from "../../api/ingest";
import { Badge } from "../../components/catalog/fields";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

/** Upload a receipt photo and watch its job; a finished job links to its review. */
export function ReceiptsPage() {
  usePageTitle("Receipts");
  const upload = useUploadReceipt();
  const jobs = useIngestJobs();
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
        onSuccess: () => {
          if (fileRef.current) fileRef.current.value = "";
        },
      },
    );
  };

  return (
    <>
      <PageHeader title="Receipts" />
      <div className="flex flex-col gap-6">
        <Card>
          <form onSubmit={submit} aria-labelledby="upload-heading" className="flex flex-col gap-3" noValidate>
            <h2 id="upload-heading" className="text-lg font-medium">
              Upload a receipt
            </h2>
            {invalid ? <Alert tone="error">{invalid}</Alert> : null}
            {upload.error ? <Alert tone="error">{errorMessage(upload.error)}</Alert> : null}
            {upload.isSuccess ? (
              <Alert tone="success">Uploaded. The receipt is being read; its job is listed below and this page refreshes on its own.</Alert>
            ) : null}
            <div className="flex flex-col gap-1">
              <label htmlFor="receipt-image" className="text-sm font-medium">
                Photo
              </label>
              <input id="receipt-image" ref={fileRef} type="file" accept="image/*" capture="environment" className={`text-sm ${focusRing}`} />
              <p className="text-xs text-neutral-600 dark:text-neutral-400">The photo stays on this deployment; nothing is sent elsewhere.</p>
            </div>
            <div>
              <Button type="submit" disabled={upload.isPending}>
                {upload.isPending ? "Uploading…" : "Upload"}
              </Button>
            </div>
          </form>
        </Card>

        <Card>
          <h2 className="mb-3 text-lg font-medium">Jobs</h2>
          {jobs.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading…
            </p>
          ) : jobs.isError ? (
            <Alert tone="error">{errorMessage(jobs.error)}</Alert>
          ) : (jobs.data ?? []).length === 0 ? (
            <EmptyState title="No receipts yet">Upload a photo and its lines will be parsed for review.</EmptyState>
          ) : (
            <ul aria-label="Ingest jobs" className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {(jobs.data ?? []).map((j) => (
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

function JobRow({ job }: { job: IngestJob }) {
  const retry = useRetryJob();
  const toManual = useJobToManual();
  const tone = job.status === "done" ? "good" : job.status === "failed" ? "warn" : "neutral";
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm" data-testid="ingest-job" data-status={job.status}>
      <span className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>{statusText[job.status] ?? job.status}</Badge>
        {job.stage && jobInFlight(job) ? <span className="text-neutral-600 dark:text-neutral-400">stage {job.stage}</span> : null}
        {job.created_at ? <time dateTime={job.created_at}>{formatDateTime(job.created_at)}</time> : null}
        {job.last_error ? <span className="text-red-700 dark:text-red-300">{job.last_error}</span> : null}
      </span>
      <span className="flex flex-wrap gap-1">
        {job.purchase_id ? (
          <Link to={`/purchases/${job.purchase_id}`} className={`inline-flex min-h-8 items-center rounded-md px-2 text-sm font-medium underline ${focusRing}`}>
            Review purchase
          </Link>
        ) : null}
        {job.status === "failed" ? (
          <Button variant="secondary" className="min-h-8 px-2 text-xs" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>
            Retry
          </Button>
        ) : null}
        {job.status === "failed" || job.status === "pending" ? (
          <Button variant="secondary" className="min-h-8 px-2 text-xs" disabled={toManual.isPending} onClick={() => toManual.mutate(job.id)}>
            Enter by hand
          </Button>
        ) : null}
      </span>
    </div>
  );
}
