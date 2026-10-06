import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import { createUploadBatch, useIngestJob, useJobToManual, useRemoveJob, useRetryJob, useUploadBatches, useUploadReceipt, type BatchReceipt, type IngestJob, type ReceiptUploadResult, type UploadBatch } from "../../api/ingest";
import { Badge } from "../../components/catalog/fields";
import { ReceiptImage } from "../../components/purchases/ReceiptImage";
import { TrustBadge } from "../../components/purchases/TrustBadge";
import { formatMoney } from "../../lib/decimal";
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
  const batches = useUploadBatches();
  // An inbox item names its job (?job=), which may be older than the newest jobs listed.
  const [params, setParams] = useSearchParams();
  const pinnedId = params.get("job") ?? undefined;
  const pinned = useIngestJob(pinnedId, false);
  // Listed once: the history drops the job only while the pinned card is showing it.
  const hidden = pinned.data?.id;
  const fileRef = useRef<HTMLInputElement>(null);
  const [invalid, setInvalid] = useState<string | null>(null);

  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<{ done: number; of: number } | null>(null);

  // Several receipts at once (a week's shopping, a folder of PDFs): each is sent
  // on its own, in turn, and one notice says what happened to all of them.
  const send = async (files: File[]) => {
    if (files.length === 0) return setInvalid("Choose a receipt photo or PDF.");
    setInvalid(null);
    const results: ReceiptUploadResult[] = [];
    const failed: string[] = [];
    setProgress({ done: 0, of: files.length });
    // One batch for everything chosen, so the history can say what it came to
    // (issue 122). If it can't be opened, nothing is sent: a half-counted batch
    // would say less than nothing.
    let batchId: string;
    try {
      batchId = (await createUploadBatch(files.length)).id;
    } catch (error) {
      setProgress(null);
      return setInvalid(`Not uploaded: ${errorMessage(error)}`);
    }
    for (const file of files) {
      try {
        results.push(await upload.mutateAsync({ image: file, batch_id: batchId }));
      } catch (error) {
        failed.push(`${file.name}: ${errorMessage(error)}`);
      }
      setProgress({ done: results.length + failed.length, of: files.length });
    }
    setProgress(null);
    if (fileRef.current) fileRef.current.value = "";
    if (failed.length > 0) setInvalid(failed.length === 1 ? `Not uploaded: ${failed[0]}` : `${failed.length} weren't uploaded. ${failed.join("; ")}`);
    if (results.length === 1 && failed.length === 0) {
      const result = results[0];
      if (result.revived) notice.show({ tone: "info", message: "This receipt was removed before; it's being read again." });
      else if (result.created === false)
        // Nothing new is read: saying "once it's read" sent people waiting for nothing.
        notice.show({
          tone: "info",
          message: "You've uploaded this receipt before, so it isn't read again.",
          action: result.job.purchase_id ? { label: "Open its purchase", to: `/shop/purchases/${result.job.purchase_id}` } : undefined,
        });
      else notice.show({ tone: "success", message: "Receipt uploaded. It'll appear in Needs you once it's read." });
    } else if (results.length > 1) {
      const fresh = results.filter((r) => r.created !== false && !r.revived).length;
      const again = results.filter((r) => r.revived).length;
      const seen = results.length - fresh - again;
      const parts: string[] = [];
      if (fresh > 0) parts.push(`Uploaded ${fresh} ${fresh === 1 ? "receipt" : "receipts"}.`);
      if (again > 0) parts.push(`${again} removed before ${again === 1 ? "is" : "are"} being read again.`);
      if (fresh + again > 0) parts.push(`${fresh + again === 1 ? "It" : "They"}'ll appear in Needs you once read.`);
      if (seen > 0) parts.push(`${seen} ${seen === 1 ? "was" : "were"} uploaded before and ${seen === 1 ? "isn't" : "aren't"} read again.`);
      notice.show({ tone: fresh + again > 0 ? "success" : "info", message: parts.join(" ") });
    }
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void send([...(fileRef.current?.files ?? [])]);
  };
  const onDrop = (event: DragEvent<HTMLFormElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!progress) void send([...event.dataTransfer.files]);
  };
  const busy = progress !== null;

  return (
    <>
      <PageHeader title="Receipts" description="Upload receipts and their lines are read for you to review." />
      <div className="flex flex-col gap-6">
        <Card>
          <form
            onSubmit={submit}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            aria-labelledby="upload-heading"
            className={`flex flex-col gap-3 rounded-md ${dragging ? "outline-2 outline-dashed outline-offset-8 outline-neutral-400" : ""}`}
            noValidate
          >
            <h2 id="upload-heading" className="text-lg font-medium">
              Upload receipts
            </h2>
            {invalid ? <Alert tone="error">{invalid}</Alert> : null}
            {upload.error ? <Alert tone="error">{errorMessage(upload.error)}</Alert> : null}
            <div className="flex flex-col gap-1">
              <label htmlFor="receipt-image" className="text-sm font-medium">
                Receipt photos or PDFs
              </label>
              {/* The server's allowlist (app/ingest/formats.py) is the one that
                  decides; this only filters the picker, and image/* alone used to
                  hide the PDFs that emailed receipts arrive as. */}
              <input id="receipt-image" ref={fileRef} type="file" multiple accept="image/jpeg,image/png,image/webp,image/heic,application/pdf" className={`min-h-11 text-sm lg:min-h-0 ${focusRing}`} />
              <p className="text-xs text-neutral-600 dark:text-neutral-400">Choose one or several, or drop them here. They stay on this deployment; nothing is sent elsewhere.</p>
            </div>
            <div>
              <Button type="submit" disabled={busy}>
                {progress ? `Uploading ${progress.done + 1} of ${progress.of}…` : "Upload"}
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
          <h2 className="mb-3 text-lg font-medium">Upload history</h2>
          {batches.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading…
            </p>
          ) : batches.isError ? (
            <Alert tone="error">{errorMessage(batches.error)}</Alert>
          ) : batches.data.length === 0 && !pinnedId ? (
            <EmptyState title="No receipts yet">Upload a photo and its lines will be parsed for review.</EmptyState>
          ) : (
            <ol aria-label="Uploads" className="flex flex-col gap-6">
              {batches.data.map((b) => (
                <li key={b.id}>
                  <BatchSection batch={b} hidden={hidden} />
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </>
  );
}

/** A count, or a dash for none: a zero is not something to look at (issue 122). */
function tally(n: number): string {
  return n > 0 ? String(n) : "—";
}

/**
 * One upload: when, what it came to, and each receipt in it. The counts read
 * the same way as each receipt's badge, so a batch can be judged without
 * opening its drafts.
 */
function BatchSection({ batch, hidden }: { batch: UploadBatch; hidden?: string }) {
  const unsent = batch.file_count - batch.uploaded;
  const arrived = [`${batch.uploaded} ${batch.uploaded === 1 ? "receipt" : "receipts"}`];
  if (batch.already_seen > 0) arrived.push(`${batch.already_seen} uploaded before`);
  if (batch.revived > 0) arrived.push(`${batch.revived} read again`);
  if (unsent > 0) arrived.push(`${unsent} not uploaded`);
  if (batch.reading > 0) arrived.push(`${batch.reading} being read`);
  const rows = batch.receipts.filter((r) => r.job.id !== hidden);
  return (
    <section aria-label={`Uploaded ${formatDateTime(batch.created_at)}`} data-testid="upload-batch">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-neutral-200 pb-2 dark:border-neutral-800">
        <h3 className="font-medium">
          <time dateTime={batch.created_at}>{formatDateTime(batch.created_at)}</time>
        </h3>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">{arrived.join(" · ")}</p>
        <dl className="flex w-full flex-wrap gap-x-4 text-sm" aria-label="What the readings came to">
          <div className="flex gap-1">
            <dt>Add up</dt>
            <dd className="font-medium tabular-nums">{tally(batch.adds_up)}</dd>
          </div>
          <div className="flex gap-1">
            <dt>To check</dt>
            <dd className="font-medium tabular-nums">{tally(batch.check_lines)}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Couldn&apos;t read</dt>
            <dd className="font-medium tabular-nums">{tally(batch.couldnt_read)}</dd>
          </div>
        </dl>
      </div>
      <ul aria-label="Receipts in this upload" className="divide-y divide-neutral-200 dark:divide-neutral-800">
        {rows.map((r) => (
          <li key={r.job.id} className="py-2">
            <JobRow job={r.job} receipt={r} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** "Gullwing Grocer · 9.80 · 3 lines", from what is known once it is read. */
function receiptFacts(receipt: BatchReceipt): string | null {
  const facts: string[] = [];
  if (receipt.store) facts.push(receipt.store);
  if (receipt.total) facts.push(formatMoney(receipt.total));
  if (receipt.item_lines !== null) facts.push(`${receipt.item_lines} ${receipt.item_lines === 1 ? "line" : "lines"}`);
  return facts.length > 0 ? facts.join(" · ") : null;
}

const statusText: Record<string, string> = {
  pending: "waiting to be read",
  running: "reading",
  needs_review: "ready to review",
  done: "done",
  failed: "failed",
};

/** What a running job is doing, in words; the stage codes are the pipeline's. */
const stageText: Record<string, string> = {
  captured: "starting",
  ocr: "reading the text",
  header: "finding the store and date",
  lines: "reading the lines",
  resolve: "matching products",
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

function JobRow({ job, receipt, onRemoved }: { job: IngestJob; receipt?: BatchReceipt; onRemoved?: () => void }) {
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
  // When it was last sent to be read: a receipt uploaded again shows that day.
  const when = job.uploaded_at ?? job.created_at;
  const facts = receipt ? receiptFacts(receipt) : null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm" data-testid="ingest-job" data-status={job.status}>
      {/* Which receipt this is (#28): two failed jobs otherwise read the same. */}
      {job.receipt_document_id ? (
        <ReceiptImage
          documentId={job.receipt_document_id}
          width={96}
          alt=""
          link={{ label: `Open the receipt uploaded ${when ? formatDateTime(when) : ""}`.trim() }}
          className="h-16 w-12 rounded border border-neutral-200 bg-white object-cover object-top dark:border-neutral-800"
        />
      ) : null}
      <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        <Badge tone={tone}>{jobStatusText(job)}</Badge>
        {job.status !== "failed" ? <TrustBadge trust={receipt?.trust} gap={receipt?.gap} held={receipt?.held} /> : null}
        {receipt?.outcome === "already_seen" ? <span className="text-neutral-600 dark:text-neutral-400">uploaded before</span> : null}
        {job.status === "running" && job.stage && stageText[job.stage] ? <span className="text-neutral-600 dark:text-neutral-400">{stageText[job.stage]}</span> : null}
        {when ? <time dateTime={when}>{formatDateTime(when)}</time> : null}
        {facts ? <span className="text-neutral-600 dark:text-neutral-400">{facts}</span> : null}
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
