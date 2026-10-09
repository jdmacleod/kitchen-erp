import { useEffect, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { Link, useParams } from "react-router";
import { CorrectLines } from "./CorrectLines";
import { RemovedLinesCaption } from "./RemovePurchase";
import { errorMessage } from "../../api/client";
import { isPositiveDecimal, productTitle, trimDecimal } from "../../api/catalog";
import { useLocations } from "../../api/geo";
import { locationCandidates, useIngestJob, useIngestJobs } from "../../api/ingest";
import {
  LINE_KINDS,
  isQuietLine,
  purchaseErrorMessage,
  resolutionLabel,
  useAddLine,
  fetchNextDraft,
  useCommitPurchase,
  useDeleteLine,
  useMergeLine,
  usePatchLine,
  usePatchPurchase,
  useReResolveLine,
  useRememberStoreCode,
  useRememberCode,
  useResolveLine,
  useStoreCodeOffer,
  type AcceptedKind,
  type LineAddInput,
  type LinePatchInput,
  type Purchase,
  type PurchaseHeaderInput,
  type PurchaseLine,
  type Resolution,
  type Suggestion,
} from "../../api/purchases";
import { cmp, div, formatMoney, isDecimal, isNonNegativeDecimal, stripZeros } from "../../lib/decimal";
import { LG_QUERY, useMediaQuery } from "../../lib/useMediaQuery";
import { fromDateTimeLocal, toDateTimeLocal } from "../../lib/openingHours";
import { Badge, Disclosure, SelectField, hintClass } from "../catalog/fields";
import { UnitSelect } from "../catalog/UnitSelect";
import { Alert, Button, Card, Field, focusRing, tapTarget } from "../ui";
import { BestBy } from "./BestBy";
import { ProductPicker } from "./ProductPicker";
import { ReceiptImage } from "./ReceiptImage";
import { CategoryChip } from "../CategoryChip";
import { useWidePage } from "../chrome";
import { useNotice } from "../Notice";
import { SegmentedControl } from "../SegmentedControl";

const acceptedKindOf: Record<Suggestion["kind"], AcceptedKind> = { alias_unconfirmed: "alias", fuzzy: "fuzzy", llm: "llm", similar: "similar", code: "code" };

const ATTACHABLE = new Set(["discount", "deposit"]);

const SHORTCUTS: [string, string][] = [
  ["j / k", "next / previous line"],
  ["Enter", "accept the top suggestion"],
  ["/", "choose or create a product"],
  ["i", "ignore the line"],
  ["e", "edit quantities and prices"],
  ["c", "commit"],
  ["Esc", "close"],
];

function isEditable(el: HTMLElement): boolean {
  return el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA" || el.isContentEditable;
}

/** The first suggestion a bare Enter would accept. */
function topSuggestion(line: PurchaseLine): Suggestion | null {
  return line.suggestions?.find((s) => s.product_id !== null || s.ignore) ?? null;
}

/**
 * Review a parsed purchase before it commits: the receipt image beside an
 * editable header and the lines, each with its resolution, suggestions, and
 * corrections. Fully keyboard-operable; the legend at the top lists the keys.
 */
export function ReviewPurchase({ purchase }: { purchase: Purchase }) {
  // The receipt beside a five-column table needs more than reading width (#61).
  useWidePage();
  const id = purchase.id;
  const resolve = useResolveLine(id);
  const reResolve = useReResolveLine(id);
  const patchLine = usePatchLine(id);
  const addLine = useAddLine(id);
  const deleteLine = useDeleteLine(id);
  const mergeLine = useMergeLine(id);
  const commit = useCommitPurchase(id);
  const mutations = [resolve, reResolve, patchLine, addLine, deleteLine, mergeLine, commit];
  const busy = mutations.some((m) => m.isPending);
  const error = mutations.find((m) => m.error)?.error;

  const lines = useMemo(() => [...purchase.lines].sort((a, b) => a.seq - b.seq), [purchase.lines]);
  const itemLines = lines.filter((l) => l.line_kind === "item");
  // Far off its printed total (issue 121, ruling R2): the review opens on the gap
  // and the lines most likely to explain it.
  const carefulLook = purchase.held === true;
  const [currentId, setCurrentId] = useState<string | null>(
    () => ((carefulLook ? lines.find(suspectLine) : undefined) ?? lines.find((l) => !isQuietLine(l)) ?? lines[0])?.id ?? null,
  );
  const [picking, setPicking] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  // "Correct the lines" (issue 182): the whole draft in one table, saved at once.
  const [correcting, setCorrecting] = useState(false);
  const notice = useNotice();
  // Cards below lg, the table at lg and wider (G18). One or the other is in the
  // document, so each line's ids stay unique.
  const liveWide = useMediaQuery(LG_QUERY);
  // The layout holds while a line is being edited or given a product: crossing
  // the breakpoint would remount the editor and drop what was typed.
  const holding = editing !== null || picking !== null;
  const [heldWide, setHeldWide] = useState(liveWide);
  if (!holding && heldWide !== liveWide) setHeldWide(liveWide);
  const wide = holding ? heldWide : liveWide;
  const [filter, setFilter] = useState<"needs" | "all">("all");
  const needing = lines.filter(needsYou);
  // What is on screen, in order: on a phone the lines that need you come first.
  // A receipt held for a careful look puts its suspect lines first at every width.
  const shown = useMemo(() => {
    const visible = filter === "needs" ? lines.filter(needsYou) : lines;
    const ordered = wide ? visible : [...visible.filter(needsYou), ...visible.filter((l) => !needsYou(l))];
    return carefulLook ? [...ordered.filter(suspectLine), ...ordered.filter((l) => !suspectLine(l))] : ordered;
  }, [lines, filter, wide, carefulLook]);

  // The page stays and turns Committed, with how many prices the commit added and,
  // when drafts remain, a way to the next one (G9).
  const announceCommit = async (committed: Purchase) => {
    // Only the prices this commit made. A recommit keeps the observations of
    // lines that did not change, and those were in the price book already.
    const before = new Set(purchase.lines.map((l) => l.observation_id).filter(Boolean));
    const n = committed.lines.filter((l) => l.observation_id && !before.has(l.observation_id)).length;
    // Lines with no product emit no price: they wait in To identify, and on a
    // household's first receipts that is every line. "Already in the price
    // book" was said then, when nothing had been added at all.
    const waiting = committed.lines.filter((l) => l.line_kind === "item" && !l.product && l.resolution !== "ignored").length;
    const lines = `${waiting} ${waiting === 1 ? "line waits" : "lines wait"} in To identify`;
    const priced = committed.lines.some((l) => l.observation_id);
    const message =
      n > 0
        ? `Committed. ${n} ${n === 1 ? "price" : "prices"} added to the price book.${waiting > 0 ? ` ${lines}.` : ""}`
        : !priced && waiting > 0
          ? `Committed. No prices yet: ${lines}, and each one's price is added once it's named.`
          : `Committed. Its prices were already in the price book.${waiting > 0 ? ` ${lines}.` : ""}`;
    // Said at once. "Next draft" joins it when the lookup answers, and only if
    // this notice is still showing: a reopen in the meantime dismisses it.
    const id = notice.show({
      tone: "success",
      message,
      action: waiting > 0 ? { label: "Name them", to: "/shop/receipts/identify?mode=name" } : undefined,
    });
    const next = await fetchNextDraft(committed.id).catch(() => null);
    if (next) notice.update(id, { action: { label: "Next draft", to: `/shop/purchases/${next.id}` } });
  };

  // Something to focus once the next render has put it in the document.
  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    pendingFocus.current = null;
    document.getElementById(target)?.focus();
  });
  const rowId = (lineId: string) => `review-line-${lineId}`;
  const focusRow = (lineId: string | null) => {
    if (lineId) pendingFocus.current = rowId(lineId);
  };

  // Always a line on screen: when the filter hides the current one, the first
  // shown takes over, so a shortcut never acts on a line nobody can see.
  const current = shown.find((l) => l.id === currentId) ?? shown[0] ?? null;
  const move = (delta: number) => {
    if (shown.length === 0) return;
    const index = Math.max(0, shown.findIndex((l) => l.id === current?.id));
    const next = shown[Math.min(shown.length - 1, Math.max(0, index + delta))];
    setCurrentId(next.id);
    document.getElementById(rowId(next.id))?.focus();
  };

  const accept = (line: PurchaseLine, s: Suggestion) => {
    const accepted_kind = acceptedKindOf[s.kind];
    if (s.ignore) resolve.mutate({ lineId: line.id, ignore: true, accepted_kind });
    else if (s.product_id) resolve.mutate({ lineId: line.id, product_id: s.product_id, accepted_kind });
  };
  const ignore = (line: PurchaseLine) => resolve.mutate({ lineId: line.id, ignore: true });
  const choose = (line: PurchaseLine, productId: string) =>
    resolve.mutate(
      { lineId: line.id, product_id: productId },
      {
        onSuccess: () => {
          setPicking(null);
          focusRow(line.id);
        },
      },
    );
  const openPicker = (line: PurchaseLine) => {
    setPicking(line.id);
    setEditing(null);
    pendingFocus.current = `review-pick-${line.id}`;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // The table has keys of its own; a stray "c" there must not open Commit.
    if (correcting) return;
    const target = event.target as HTMLElement;
    if (event.key === "Escape") {
      if (picking || editing || confirming) {
        event.preventDefault();
        event.stopPropagation();
        setPicking(null);
        setEditing(null);
        setConfirming(false);
        focusRow(current?.id ?? null);
      }
      return;
    }
    if (isEditable(target) || event.metaKey || event.ctrlKey || event.altKey) return;
    if (target.tagName === "BUTTON" && (event.key === "Enter" || event.key === " ")) return;
    switch (event.key) {
      case "j":
      case "ArrowDown":
        event.preventDefault();
        move(1);
        return;
      case "k":
      case "ArrowUp":
        event.preventDefault();
        move(-1);
        return;
      case "Enter": {
        if (!current) return;
        const s = topSuggestion(current);
        if (s) {
          event.preventDefault();
          accept(current, s);
        }
        return;
      }
      case "/":
        if (!current || busy) return;
        event.preventDefault();
        openPicker(current);
        return;
      case "i":
        if (!current || busy || current.resolution === "ignored") return;
        event.preventDefault();
        ignore(current);
        return;
      case "e":
        if (!current) return;
        event.preventDefault();
        setEditing(current.id);
        setPicking(null);
        pendingFocus.current = `review-edit-${current.id}-qty`;
        return;
      case "c":
        if (busy) return;
        event.preventDefault();
        setConfirming(true);
        return;
      default:
        return;
    }
  };

  // The item lines printed directly above and below a line: where a weight or
  // count on a row of its own can be merged (#87).
  const neighbours = (line: PurchaseLine) => {
    const at = lines.indexOf(line);
    const item = (l: PurchaseLine | undefined) => (l && l.line_kind === "item" ? l : null);
    return { above: item(lines[at - 1]), below: item(lines[at + 1]) };
  };

  const lineProps = (line: PurchaseLine): ReviewLineProps => ({
    line,
    itemLines,
    mergeInto: line.flags.includes("quantity_line") ? neighbours(line) : { above: null, below: null },
    current: line.id === current?.id,
    picking: picking === line.id,
    editing: editing === line.id,
    busy,
    onFocus: () => setCurrentId(line.id),
    onAccept: (s) => accept(line, s),
    onIgnore: () => ignore(line),
    onOpenPicker: () => openPicker(line),
    onClosePicker: () => {
      setPicking(null);
      focusRow(line.id);
    },
    onChoose: (productId) => choose(line, productId),
    onReResolve: () => reResolve.mutate(line.id),
    onEdit: () => {
      setEditing(line.id);
      pendingFocus.current = `review-edit-${line.id}-qty`;
    },
    onPatch: (input) =>
      patchLine.mutate(
        { lineId: line.id, ...input },
        {
          onSuccess: () => {
            setEditing(null);
            focusRow(line.id);
          },
        },
      ),
    onCancelEdit: () => {
      setEditing(null);
      focusRow(line.id);
    },
    onDelete: () => deleteLine.mutate(line.id),
    onMerge: (into) => mergeLine.mutate({ lineId: line.id, intoLineId: into.id }, { onSuccess: () => focusRow(into.id) }),
  });

  // An ignored line has no product on purpose; it never joins the to-identify queue.
  const unresolved = itemLines.filter(
    (l) => l.resolution !== "ignored" && (l.resolution === "unmatched" || !l.product),
  ).length;
  // Neither does it emit a price: what commits is the item lines less both.
  const emitting = itemLines.filter((l) => l.resolution !== "ignored").length - unresolved;
  const mismatch = purchase.flags.some((f) => f === "reconcile_mismatch" || f === "total_mismatch");
  const suspectCount = lines.filter(suspectLine).length;
  // What the reader could not find is filled with a stand-in, and a stand-in
  // looks like an answer in the header fields. Saving the header clears these.
  const dateMissing = purchase.flags.includes("purchased_at_missing");
  // A long receipt is read in parts; one that never answered leaves a gap (#60).
  const linesPartial = purchase.flags.includes("lines_partial");
  // Rows the scan prints like purchases that no line accounts for (issue 181).
  const rowsNotRead = purchase.flags.includes("rows_not_read") && purchase.status !== "committed";
  // Lines read without their decimal point, when putting it back makes the lines
  // match the printed total (#59). Offered, never applied without a click.
  const decimalSuspects = purchase.flags.includes("decimals_restore_total")
    ? lines.filter((l) => l.flags.includes("decimal_missing") && l.line_total !== null)
    : [];
  const [restoring, setRestoring] = useState(false);
  const restoreDecimals = async () => {
    setRestoring(true);
    try {
      for (const l of decimalSuspects) await patchLine.mutateAsync({ lineId: l.id, line_total: restoredTotal(l.line_total!) });
    } finally {
      setRestoring(false);
    }
  };
  const totalMissing = purchase.flags.includes("total_missing");
  // The image transcriber was set up but could not read this receipt (04, 2O):
  // information, not a warning, and gone once the purchase is committed.
  const readFromTextScan = purchase.flags.includes("ocr_fallback") && purchase.status !== "committed";

  return (
    <div onKeyDown={onKeyDown} className="flex flex-col gap-4" data-testid="review">
      {readFromTextScan ? <p className="text-sm text-neutral-600 dark:text-neutral-400">Read from the text scan: the image reader wasn't available.</p> : null}
      <dl aria-label="Keyboard shortcuts" className="hidden flex-wrap gap-x-4 lg:flex gap-y-1 text-xs text-neutral-600 dark:text-neutral-400">
        {SHORTCUTS.map(([key, what]) => (
          <div key={key} className="inline-flex items-center gap-1">
            <dt>
              <kbd className="rounded border border-neutral-300 bg-neutral-100 px-1 font-mono text-[11px] dark:border-neutral-700 dark:bg-neutral-800">{key}</kbd>
            </dt>
            <dd>{what}</dd>
          </div>
        ))}
      </dl>

      {error ? <Alert tone="error">{purchaseErrorMessage(error)}</Alert> : null}
      {carefulLook ? (
        <Alert tone="warn">
          <span className="block font-medium">This receipt needs a careful look</span>
          <span className="block">
            Lines add up to {formatMoney(purchase.lines_total ?? purchase.computed_total)}; the receipt says {formatMoney(purchase.total)}.
            {purchase.flags.includes("total_not_in_scan") ? " That total is not on the scan either, so check it against the photo." : ""}
            {suspectCount > 0 ? ` The ${suspectCount === 1 ? "line most likely to explain it is" : `${suspectCount} lines most likely to explain it are`} shown first.` : " Compare the lines with the receipt image."}
          </span>
        </Alert>
      ) : mismatch ? (
        <Alert tone="info">
          The receipt says {purchase.total !== null ? formatMoney(purchase.total) : "—"} but the lines add up to{" "}
          {purchase.computed_total !== null ? formatMoney(purchase.computed_total) : "—"}. Check the lines or the header.
        </Alert>
      ) : null}
      {decimalSuspects.length > 0 ? (
        <Alert tone="info">
          <span className="block">
            {decimalSuspects.length === 1 ? "1 line looks like it lost its decimal point" : `${decimalSuspects.length} lines look like they lost their decimal points`} ({formatMoney(decimalSuspects[0].line_total)} for{" "}
            {formatMoney(restoredTotal(decimalSuspects[0].line_total!))}). With the decimal points back, the lines add up to the receipt total.
          </span>
          <Button variant="secondary" className="mt-2" disabled={busy || restoring} onClick={() => void restoreDecimals()}>
            {restoring ? "Restoring…" : decimalSuspects.length === 1 ? "Restore the decimal point" : `Restore ${decimalSuspects.length} decimal points`}
          </Button>
        </Alert>
      ) : null}
      {linesPartial ? (
        <Alert tone="info">
          Part of this receipt could not be read, so some of its lines are missing. Compare the lines with the receipt image and add the ones that are not here.
        </Alert>
      ) : null}
      {rowsNotRead && !linesPartial ? (
        <Alert tone="info">
          Some rows on the receipt print an amount but were not read as lines. Compare the lines with the receipt image and add any that are missing.
        </Alert>
      ) : null}
      {dateMissing ? (
        <Alert tone="info">
          The date could not be read from the receipt, so Purchased at is when it was uploaded. Set it from the receipt and save the header: every price here is recorded on that date.
        </Alert>
      ) : null}
      {totalMissing ? (
        <Alert tone="info">
          The total could not be read from the receipt, so Total is what the lines add up to. Enter the printed total and save the header, and a line that was misread will show up as a mismatch.
        </Alert>
      ) : null}

      <div className={`grid gap-4 ${purchase.receipt_document_id ? "md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:grid-cols-[minmax(0,1fr)_minmax(0,3fr)] xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]" : ""}`}>
        {purchase.receipt_document_id ? (
          <Disclosure summary="Receipt image" defaultOpen className="md:sticky md:top-4 md:self-start">
            <ReceiptImage
              documentId={purchase.receipt_document_id}
              alt="The receipt as photographed"
              // A phone photo or a PDF page is several megabytes at full size; the
              // review needs a screen's width, and the link opens the original.
              width={1024}
              link={{ label: "Open the receipt photo at full size" }}
              className="max-h-[80dvh] w-full rounded-md border border-neutral-200 object-contain dark:border-neutral-800"
            />
          </Disclosure>
        ) : null}

        <div className="flex min-w-0 flex-col gap-4">
          <Card>
            <ReviewHeader key={purchase.updated_at} purchase={purchase} />
          </Card>

          <Card>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-lg font-medium">Lines</h2>
              <span className="flex flex-wrap items-center gap-2">
                <span className={hintClass}>
                  {itemLines.length} items · {unresolved} to identify
                </span>
                {correcting ? null : (
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => {
                      setCorrecting(true);
                      setPicking(null);
                      setEditing(null);
                    }}
                  >
                    Correct the lines
                  </Button>
                )}
              </span>
            </div>
            {correcting ? (
              <CorrectLines purchase={purchase} onDone={() => setCorrecting(false)} />
            ) : (
              <>
                <div className="mb-3">
                  <SegmentedControl
                    label="Show lines"
                    options={[
                      { value: "needs", label: `Needs you ${needing.length}` },
                      { value: "all", label: `All ${lines.length}` },
                    ]}
                    value={filter}
                    onChange={setFilter}
                  />
                </div>
                {shown.length === 0 ? (
                  <p className={`py-4 ${hintClass}`}>Nothing here needs you. Every line is matched or ignored.</p>
                ) : wide ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm" aria-label="Receipt lines">
                      <thead>
                        <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                          <th className="py-2 pr-2">#</th>
                          <th className="py-2 pr-2">Receipt says</th>
                          <th className="py-2 pr-2">Parsed</th>
                          <th className="py-2 pr-2">Product</th>
                          <th className="py-2">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                        {shown.map((line) => (
                          <ReviewLine key={line.id} {...lineProps(line)} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <ul aria-label="Receipt lines" className="flex flex-col gap-2">
                    {shown.map((line) => (
                      <ReviewCard key={line.id} {...lineProps(line)} />
                    ))}
                  </ul>
                )}
                <RemovedLinesCaption purchase={purchase} />
                <Disclosure summary="Add a line" className="mt-3">
                  <AddLineForm itemLines={itemLines} busy={busy} onAdd={(input) => addLine.mutate(input)} />
                </Disclosure>
              </>
            )}
          </Card>

          {/* Below lg, Commit sits in the thumb zone just above the tab bar (G18). */}
          <div className="sticky bottom-[calc(6rem+env(safe-area-inset-bottom))] z-10 flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 bg-neutral-50/95 p-3 shadow-sm backdrop-blur lg:bottom-4 dark:border-neutral-800 dark:bg-neutral-950/95">
            <Button onClick={() => setConfirming(true)} disabled={busy || correcting} className="min-h-12 flex-1 text-base lg:min-h-10 lg:flex-none lg:text-sm">
              Commit purchase
            </Button>
            <span className={hintClass}>
              {correcting
                ? "Save or cancel your corrections before committing."
                : unresolved > 0 ? `${unresolved} unidentified ${unresolved === 1 ? "line" : "lines"} will go to the to-identify queue.` : "Every item line has a product."}
            </span>
          </div>
        </div>
      </div>

      {confirming ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onMouseDown={() => setConfirming(false)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="commit-dialog-title"
            className="w-full max-w-md rounded-lg border border-neutral-200 bg-white p-4 shadow-lg dark:border-neutral-800 dark:bg-neutral-900"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <h2 id="commit-dialog-title" className="text-lg font-medium">
              Commit this purchase?
            </h2>
            <p className="mt-1 text-sm text-neutral-700 dark:text-neutral-300">
              {emitting} {emitting === 1 ? "line emits" : "lines emit"} a price observation now.
              {unresolved > 0 ? ` ${unresolved} unidentified ${unresolved === 1 ? "line waits" : "lines wait"} in the to-identify queue.` : ""}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                autoFocus
                disabled={commit.isPending}
                onClick={() =>
                  commit.mutate(undefined, {
                    onSuccess: (committed) => {
                      setConfirming(false);
                      void announceCommit(committed);
                    },
                    onError: () => setConfirming(false),
                  })
                }
              >
                {commit.isPending ? "Committing…" : "Commit"}
              </Button>
              <Button variant="secondary" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// --- header -----------------------------------------------------------------

function ReviewHeader({ purchase }: { purchase: Purchase }) {
  const patch = usePatchPurchase(purchase.id);
  const locations = useLocations({});
  // The ingest job that produced this purchase, for ranked location candidates.
  const jobs = useIngestJobs("", purchase.source === "receipt");
  const jobId = jobs.data?.find((j) => j.purchase_id === purchase.id)?.id;
  const job = useIngestJob(jobId);
  const candidates = locationCandidates(job.data);
  const addressOf = (id: string) => (locations.data ?? []).find((l) => l.id === id)?.address ?? null;

  const [form, setForm] = useState({
    vendor_location_id: purchase.vendor_location?.id ?? "",
    purchased_at: toDateTimeLocal(new Date(purchase.purchased_at)),
    subtotal: editableMoney(purchase.subtotal),
    tax: editableMoney(purchase.tax),
    total: editableMoney(purchase.total),
  });
  const [invalid, setInvalid] = useState<string | null>(null);
  // What the last Save did, until the form changes again: a click with no
  // answer left people unsure the location had stuck.
  const [saved, setSaved] = useState<"saved" | "unchanged" | null>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setSaved(null);
    setForm((f) => ({ ...f, [key]: value }));
  };

  const save = () => {
    const input: PurchaseHeaderInput = {};
    if (form.vendor_location_id && form.vendor_location_id !== (purchase.vendor_location?.id ?? "")) input.vendor_location_id = form.vendor_location_id;
    const at = fromDateTimeLocal(form.purchased_at);
    if (!at) return setInvalid("Enter a valid date and time.");
    if (form.purchased_at !== toDateTimeLocal(new Date(purchase.purchased_at))) input.purchased_at = at;
    for (const key of ["subtotal", "tax", "total"] as const) {
      const value = form[key].trim();
      if (value !== "" && !isNonNegativeDecimal(value)) return setInvalid(`${key[0].toUpperCase()}${key.slice(1)} must be a number.`);
      if (value !== "" && !sameAmount(value, purchase[key])) input[key] = value;
    }
    setInvalid(null);
    if (Object.keys(input).length === 0) return setSaved("unchanged");
    patch.mutate(input, { onSuccess: () => setSaved("saved") });
  };

  return (
    <div className="flex flex-col gap-3" role="group" aria-label="Purchase header">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Header</h2>
        <span className={hintClass}>
          Lines add up to {purchase.computed_total !== null ? formatMoney(purchase.computed_total) : "—"}
        </span>
      </div>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {patch.error ? <Alert tone="error">{purchaseErrorMessage(patch.error)}</Alert> : null}
      {candidates.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium">Location candidates from the receipt</span>
          <ul aria-label="Location candidates" className="flex flex-wrap gap-2">
            {candidates.map((c) => (
              <li key={c.location_id}>
                <Button
                  variant={form.vendor_location_id === c.location_id ? "primary" : "secondary"}
                  className="min-h-11 lg:min-h-8 px-2 text-xs"
                  onClick={() => set("vendor_location_id", c.location_id)}
                  aria-pressed={form.vendor_location_id === c.location_id}
                  // Like the fields below: a choice made mid-save would not be
                  // in the request, yet "Saved." would follow it.
                  disabled={patch.isPending}
                >
                  <span className="flex flex-col items-start text-left">
                    <span>{c.vendor_name === c.name ? c.name : `${c.vendor_name} — ${c.name}`}</span>
                    {/* Branches of one chain read alike; where each one is tells them apart (#86). */}
                    {addressOf(c.location_id) ? <span className="text-xs font-normal">{addressOf(c.location_id)}</span> : null}
                  </span>
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField id="review-location" label="Location" value={form.vendor_location_id} onChange={(e) => set("vendor_location_id", e.target.value)} disabled={patch.isPending}>
          <option value="">Choose a location</option>
          {(locations.data ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name === l.vendor.name ? l.name : `${l.vendor.name} — ${l.name}`}
            </option>
          ))}
        </SelectField>
        <Field id="review-purchased-at" label="Purchased at" type="datetime-local" value={form.purchased_at} onChange={(e) => set("purchased_at", e.target.value)} disabled={patch.isPending} />
      </div>
      <RememberStoreCode purchase={purchase} />
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id="review-subtotal" label="Subtotal" inputMode="decimal" autoComplete="off" value={form.subtotal} onChange={(e) => set("subtotal", e.target.value)} disabled={patch.isPending} />
        <Field id="review-tax" label="Tax" inputMode="decimal" autoComplete="off" value={form.tax} onChange={(e) => set("tax", e.target.value)} disabled={patch.isPending} />
        <Field id="review-total" label="Total" inputMode="decimal" autoComplete="off" value={form.total} onChange={(e) => set("total", e.target.value)} disabled={patch.isPending} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={save} disabled={patch.isPending}>
          {patch.isPending ? "Saving…" : "Save header"}
        </Button>
        <span role="status" className={hintClass}>
          {saved === "saved" ? "Saved." : saved === "unchanged" ? "Nothing has changed." : ""}
        </span>
      </div>
    </div>
  );
}

/**
 * After the location is saved, a quiet offer to remember the store code the
 * receipt printed, so the next receipt from that store matches on its own (1F,
 * design D10). The server offers only codes that read like a store number, and
 * shows the line they were printed on with other long numbers masked.
 */
function RememberStoreCode({ purchase }: { purchase: Purchase }) {
  const locationId = purchase.vendor_location?.id ?? null;
  const reviewing = purchase.source === "receipt" && (purchase.status === "draft" || purchase.status === "reviewed");
  const offer = useStoreCodeOffer(purchase.id, locationId, reviewing);
  const remember = useRememberStoreCode(purchase.id);
  const done = remember.data && remember.data.location_id === locationId ? remember.data : null;
  // A committed purchase keeps this page mounted; the offer is for review only.
  if (!reviewing) return null;
  if (done) {
    return (
      <p role="status" className="text-sm text-green-800 dark:text-green-300">
        Remembered {done.code} for {done.location_name}.
      </p>
    );
  }
  if (!offer.data) return null;
  const { code, location_name, printed_line } = offer.data;
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <p className={hintClass}>
        Printed on this receipt: <span className="rounded bg-neutral-100 px-1 font-mono text-neutral-900 dark:bg-neutral-800 dark:text-neutral-100">{printed_line}</span>
      </p>
      <div className="flex flex-col gap-1">
        <Button variant="secondary" className="min-h-11 lg:min-h-8 px-2 text-xs" disabled={remember.isPending} onClick={() => remember.mutate(code)}>
          {remember.isPending ? "Remembering…" : `Remember ${code} for ${location_name}`}
        </Button>
        {remember.isError ? (
          <p role="alert" className="text-xs text-red-700 dark:text-red-300">
            {purchaseErrorMessage(remember.error)}
          </p>
        ) : null}
      </div>
    </div>
  );
}

// --- lines ------------------------------------------------------------------

interface ReviewLineProps {
  line: PurchaseLine;
  itemLines: PurchaseLine[];
  mergeInto: { above: PurchaseLine | null; below: PurchaseLine | null };
  current: boolean;
  picking: boolean;
  editing: boolean;
  busy: boolean;
  onFocus: () => void;
  onAccept: (s: Suggestion) => void;
  onIgnore: () => void;
  onOpenPicker: () => void;
  onClosePicker: () => void;
  onChoose: (productId: string) => void;
  onReResolve: () => void;
  onEdit: () => void;
  onPatch: (input: LinePatchInput) => void;
  onCancelEdit: () => void;
  onDelete: () => void;
  onMerge: (into: PurchaseLine) => void;
}

function lineTitle(l: PurchaseLine): string {
  return l.product ? productTitle(l.product) : (l.raw_text ?? `line ${l.seq}`);
}

/**
 * A line that needs a person: flagged, or an item with no product or with
 * suggestions to weigh. Not ignored lines, and not ones matched automatically.
 */
export function needsYou(line: PurchaseLine): boolean {
  if (isQuietLine(line)) return false;
  const isItem = line.line_kind === "item";
  return line.flags.length > 0 || (isItem && line.resolution !== "ignored" && (!line.product || (line.suggestions?.length ?? 0) > 0));
}

/** Mirrors backend PRICE_FLAGS: a suspected misreading, cleared when a person gives the price. */
const PRICE_FLAGS = ["decimal_missing", "exceeds_total", "tax_code_as_digit", "no_amount_printed", "regular_price_from_text", "tax_from_rate", "not_in_scan", "points_not_money", "payment_row", "continuation_row", "rate_note", "saving_already_netted", "deposit_rate_row", "deposit_total"];

/** A line whose amount, or whether it is a line at all, is in doubt: shown first on a receipt held for a careful look. */
function suspectLine(line: PurchaseLine): boolean {
  return line.flags.some((f) => PRICE_FLAGS.includes(f) || f === "footer_text");
}

/** A stored amount ("6.9800") as it is typed ("6.98"); no digit that matters is dropped. */
function editableMoney(stored: string | null | undefined): string {
  return stored === null || stored === undefined ? "" : stripZeros(stored, 2);
}

/**
 * Whether typed text is the stored amount. Compared as numbers, so "6.98"
 * against "6.9800" is not an edit: sending it would clear the line's price
 * flags, or the header's missing-total flag, that nobody answered.
 */
function sameAmount(text: string, stored: string | null | undefined): boolean {
  return stored !== null && stored !== undefined && isDecimal(text) && cmp(text, stored) === 0;
}

/** What a line flag means, in words; unknown flags fall back to their code. */
const FLAG_LABELS: Record<string, string> = {
  // #31: the quantity is the default, or the line looked weighed but could not be read.
  qty_assumed: "quantity assumed",
  qty_corrected: "quantity from the print",
  qty_inferred: "quantity from the print",
  // #87: a weight or count printed on a row of its own, joined to its item or not.
  qty_from_line_above: "quantity from above",
  qty_from_line_below: "quantity from below",
  quantity_line: "only a quantity",
  // A weight row whose "lb" OCR garbled, joined by its arithmetic.
  unit_misread: "pounds, unit misread",
  // #59: a price printed with no decimal point, or a line over the whole receipt.
  decimal_missing: "decimal point missing?",
  exceeds_total: "more than the receipt total",
  // A tax letter OCR read as a third decimal ("6.378"), cut back to the cents.
  tax_code_as_digit: "tax letter read as a digit",
  // Issue 181: line structure fixed from the printed arithmetic.
  qty_from_prefix: "count printed before the name",
  no_amount_printed: "prints no amount",
  kind_from_wording: "a product, not a saving",
  regular_price_from_text: "regular price restored",
  footer_text: "footer text?",
  tax_from_rate: "tax from its rate",
  // A saving printed "8.00-", points read as money, a payment read as an item.
  negative_from_text: "printed as a saving",
  points_not_money: "points, not money",
  payment_row: "a payment, not a purchase",
  // A row of the item above (its rate, its name in another script, its code).
  continuation_row: "part of the item above",
  // A rate note ("2 @ 0.45") read as a discount; a saving the price already had.
  rate_note: "a rate note, not a saving",
  saving_already_netted: "saving already in the price",
  // A deposit's rate row, or the line totalling the deposits, read as more deposit.
  deposit_rate_row: "the deposit's rate, already counted",
  deposit_total: "the deposits' total, already counted",
  // A count or weight printed beneath the item that the reader left out.
  qty_from_text: "quantity from below",
  // Issue 121: the amount is printed nowhere in the scan's text.
  not_in_scan: "not on the scan",
  // An imported line whose quantity the export never gave: no price is recorded.
  no_price: "quantity unknown, no price",
};

/** A decimal_missing amount read as its hundredth: "349.0000" is most likely 3.49. */
function restoredTotal(lineTotal: string): string {
  return div(lineTotal, "100", 2);
}

/** On the line being worked on, the one-click fix for a lost decimal point. */
function DecimalFix({ line, busy, onPatch }: { line: PurchaseLine; busy: boolean; onPatch: (input: LinePatchInput) => void }) {
  if (!line.flags.includes("decimal_missing") || line.line_total === null) return null;
  const fixed = restoredTotal(line.line_total);
  return (
    <Button variant="secondary" className="mt-1 min-h-11 lg:min-h-8 px-2 text-xs" disabled={busy} onClick={() => onPatch({ line_total: fixed })}>
      Use {formatMoney(fixed)}
    </Button>
  );
}

/**
 * On a line that is only a weight or count, the one-click merge into the item
 * it belongs to (#87): the reader could not tell which neighbour that is.
 */
function QuantityMerge({ line, mergeInto, busy, onMerge }: { line: PurchaseLine; mergeInto: ReviewLineProps["mergeInto"]; busy: boolean; onMerge: (into: PurchaseLine) => void }) {
  if (!line.flags.includes("quantity_line")) return null;
  const { above, below } = mergeInto;
  if (!above && !below) return null;
  const size = "mt-1 min-h-11 lg:min-h-8 px-2 text-xs";
  return (
    <div className="flex flex-wrap gap-1">
      {below ? (
        <Button variant="secondary" className={size} disabled={busy} onClick={() => onMerge(below)} aria-label={`Merge line ${line.seq} into the next line, ${lineTitle(below)}`}>
          Merge into the next line
        </Button>
      ) : null}
      {above ? (
        <Button variant="secondary" className={size} disabled={busy} onClick={() => onMerge(above)} aria-label={`Merge line ${line.seq} into the line above, ${lineTitle(above)}`}>
          Merge into the line above
        </Button>
      ) : null}
    </div>
  );
}

function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag.replaceAll("_", " ");
}

/** The line's number, kind and flags. */
function LineTags({ line }: { line: PurchaseLine }) {
  return (
    <>
      <span className="tabular-nums">{line.seq}</span>
      {line.line_kind !== "item" ? (
        <>
          {" "}
          <Badge>{line.line_kind}</Badge>
        </>
      ) : null}
      {line.flags.map((f) => (
        <span key={f} className="mt-1 block">
          {/* Wrapping: an unbroken "quantity from the print" made the # column
              wider than the text, pushing Product and Actions out of view. */}
          <Badge tone="warn" wrap>
            {flagLabel(f)}
          </Badge>
        </span>
      ))}
    </>
  );
}

/** What the receipt says, verbatim and normalized; one truncated line when compact. */
function LineRaw({ line, compact }: { line: PurchaseLine; compact?: boolean }) {
  if (compact) {
    return (
      <span className="block max-w-[20rem] truncate font-mono text-xs" title={line.raw_text ?? undefined}>
        {line.raw_text ?? "—"}
      </span>
    );
  }
  return (
    <>
      <span className="font-mono text-xs break-all">{line.raw_text ?? "—"}</span>
      {line.raw_text_norm && line.raw_text_norm !== line.raw_text ? (
        <span className="block text-[11px] text-neutral-600 dark:text-neutral-400">{line.raw_text_norm}</span>
      ) : null}
    </>
  );
}

/** What it was read as: quantity × unit, and the prices; on one line when compact. */
function LineParsed({ line, compact }: { line: PurchaseLine; compact?: boolean }) {
  if (compact) {
    const qty = line.qty !== null ? `${trimDecimal(line.qty)} × ${line.unit ?? ""}` : "—";
    return <span className="tabular-nums">{line.line_total !== null ? `${qty} · ${formatMoney(line.line_total)}` : qty}</span>;
  }
  return (
    <>
      <span className="tabular-nums">{line.qty !== null ? `${trimDecimal(line.qty)} × ${line.unit ?? ""}` : "—"}</span>
      <span className="block text-xs tabular-nums">
        {line.unit_price !== null ? `${formatMoney(line.unit_price, 2, 4)} ea` : ""}
        {line.unit_price !== null && line.line_total !== null ? " · " : ""}
        {line.line_total !== null ? formatMoney(line.line_total) : ""}
      </span>
    </>
  );
}

type LinePartProps = Omit<ReviewLineProps, "current" | "onFocus">;

/**
 * The product: the one chosen, or the suggestions and the picker. On a card
 * (`touch`) each suggestion is one 44px button (G18).
 */
function LineProduct({ line, itemLines, picking, busy, onAccept, onClosePicker, onChoose, onPatch, touch, compact }: LinePartProps & { touch?: boolean; compact?: boolean }) {
  const quiet = isQuietLine(line);
  const isItem = line.line_kind === "item";
  const suggestions = line.suggestions ?? [];
  const top = topSuggestion(line);
  const resolution = line.resolution as Resolution | null;

  // A line not being worked on is one scannable line (#35): what it is, or what
  // is suggested. Its buttons appear when it becomes the current line.
  if (compact && !picking) {
    const parent = line.parent_line_id ? itemLines.find((i) => i.id === line.parent_line_id) : null;
    return (
      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
        {line.product ? (
          <span className="min-w-0 truncate font-medium">{productTitle(line.product)}</span>
        ) : resolution === "ignored" ? (
          <span className="italic">ignored</span>
        ) : isItem ? (
          <span className="font-medium text-amber-800 dark:text-amber-300">unidentified</span>
        ) : parent ? (
          <span className="text-neutral-600 dark:text-neutral-400">on #{parent.seq}</span>
        ) : (
          <span>—</span>
        )}
        {resolution && isItem ? (
          <Badge tone={resolution === "unmatched" ? "warn" : quiet ? "neutral" : "good"}>{resolutionLabel[resolution] ?? resolution}</Badge>
        ) : null}
        {top && !line.product && resolution !== "ignored" ? (
          <span className="min-w-0 truncate text-xs text-neutral-600 dark:text-neutral-400">
            Suggested: {top.ignore ? "ignore this line" : top.label}
            {suggestions.length > 1 ? ` (+${suggestions.length - 1})` : ""}
          </span>
        ) : null}
        {line.code_offer && line.product ? <RememberCode line={line} /> : null}
      </span>
    );
  }

  if (picking) {
    return (
      <div className="flex flex-col gap-1">
        <ProductPicker id={`review-pick-${line.id}`} label={`Product for line ${line.seq}`} hideLabel value={null} lineText={line.raw_text_norm ?? line.raw_text} onChange={(p) => p && onChoose(p.id)} disabled={busy} />
        <Button variant="ghost" className="min-h-11 lg:min-h-8 self-start px-2 text-xs" onClick={onClosePicker}>
          Cancel
        </Button>
      </div>
    );
  }

  const kindLabel = (s: Suggestion) =>
    s.kind === "alias_unconfirmed" ? "alias" : s.kind === "llm" ? "model" : s.kind === "code" ? "item code" : s.kind === "similar" ? "similar name" : "fuzzy";

  return (
    <>
      {line.product ? (
        <>
          <Link to={`/catalog/products/${line.product.id}`} className={`${touch ? tapTarget : ""} rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
            {productTitle(line.product)}
          </Link>{" "}
          <CategoryChip category={line.product.category} categoryKey={line.product.category_key} />
        </>
      ) : resolution === "ignored" ? (
        <span className="italic">ignored</span>
      ) : isItem ? (
        <span className="font-medium text-amber-800 dark:text-amber-300">unidentified</span>
      ) : (
        <span>—</span>
      )}
      {resolution && isItem ? (
        <span className="ml-1 text-xs">
          <Badge tone={resolution === "unmatched" ? "warn" : quiet ? "neutral" : "good"}>{resolutionLabel[resolution] ?? resolution}</Badge>
          {line.resolved_by_name ? <span className="text-neutral-600 dark:text-neutral-400"> by {line.resolved_by_name}</span> : null}
        </span>
      ) : null}
      {line.code_offer && line.product ? <RememberCode line={line} /> : null}
      {line.product && isItem ? <ReviewBestBy line={line} /> : null}
      {suggestions.length > 0 && resolution !== "ignored" && !line.product ? (
        <ul aria-label={`Suggestions for line ${line.seq}`} className={`mt-1 flex flex-col ${touch ? "gap-2" : "gap-1"}`}>
          {suggestions.map((s, i) =>
            touch ? (
              <li key={`${s.kind}-${s.product_id ?? "ignore"}-${i}`}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onAccept(s)}
                  aria-label={`Accept ${s.ignore ? "ignoring this line" : s.label}`}
                  className={`flex min-h-11 w-full items-center justify-between gap-2 rounded-lg border px-3 text-left text-sm disabled:opacity-50 ${focusRing} ${
                    s === top ? "border-neutral-400 bg-neutral-100 font-medium dark:border-neutral-600 dark:bg-neutral-800" : "border-neutral-300 dark:border-neutral-700"
                  }`}
                >
                  <span className="min-w-0">{s.ignore ? "Ignore this line" : s.label}</span>
                  <span className="flex shrink-0 items-center gap-1 text-xs text-neutral-600 dark:text-neutral-400">
                    <Badge>{kindLabel(s)}</Badge>
                    <span className="tabular-nums">{s.score}</span>
                  </span>
                </button>
              </li>
            ) : (
              <li key={`${s.kind}-${s.product_id ?? "ignore"}-${i}`} className="flex flex-wrap items-center gap-1 text-xs">
                <span className={s === top ? "font-medium" : ""}>{s.ignore ? "Ignore this line" : s.label}</span>
                <Badge>{kindLabel(s)}</Badge>
                <span className="text-neutral-600 dark:text-neutral-400 tabular-nums">{s.score}</span>
                <Button variant="secondary" className="min-h-7 px-2 text-xs" disabled={busy} onClick={() => onAccept(s)}>
                  {s === top ? "Accept (Enter)" : "Accept"}
                </Button>
              </li>
            ),
          )}
        </ul>
      ) : null}
      {ATTACHABLE.has(line.line_kind) ? (
        <label className="mt-1 flex items-center gap-1 text-xs">
          Attach to
          <select
            aria-label={`Attach line ${line.seq} to`}
            value={line.parent_line_id ?? ""}
            disabled={busy}
            onChange={(e) => onPatch(e.target.value ? { parent_line_id: e.target.value } : { clear_parent: true })}
            className={`min-h-11 lg:min-h-8 rounded-md border border-neutral-300 bg-white px-1 text-xs dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
          >
            <option value="">nothing</option>
            {itemLines.map((i) => (
              <option key={i.id} value={i.id}>
                #{i.seq} {lineTitle(i)}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </>
  );
}

/** Choose, Ignore, Re-resolve, Edit and Delete. 44px on a card, compact in the table. */
function LineActions({ line, picking, editing, busy, onOpenPicker, onIgnore, onReResolve, onEdit, onDelete, touch }: LinePartProps & { touch?: boolean }) {
  const size = touch ? "min-h-11 px-3 text-sm" : "min-h-7 px-2 text-xs";
  const resolution = line.resolution as Resolution | null;
  // A line that reached the price book asks first: deleting it voids its
  // price (#72, D12). One that never did still goes in one click.
  const [confirming, setConfirming] = useState(false);
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) keep.current?.focus();
  }, [confirming]);
  if (confirming) {
    return (
      <div role="group" aria-label={`Delete line ${line.seq}`} className="flex flex-wrap items-center gap-2 rounded-md border border-red-300 bg-red-50 p-2 dark:border-red-900 dark:bg-red-950">
        <span className="text-sm">Delete line {line.seq}? Its price is voided.</span>
        <Button variant="danger" className={size} disabled={busy} onClick={onDelete}>
          Delete
        </Button>
        <Button
          ref={keep}
          variant="secondary"
          className={size}
          onClick={() => {
            setConfirming(false);
            document.getElementById(`review-line-${line.id}`)?.focus();
          }}
        >
          Keep it
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-1">
      {line.line_kind === "item" ? (
        <>
          <Button variant="secondary" className={size} disabled={busy || picking} onClick={onOpenPicker}>
            {line.product ? "Change" : "Choose…"}
          </Button>
          {resolution !== "ignored" ? (
            <Button variant="secondary" className={size} disabled={busy} onClick={onIgnore}>
              Ignore
            </Button>
          ) : null}
          <Button variant="ghost" className={size} disabled={busy} onClick={onReResolve}>
            Re-resolve
          </Button>
        </>
      ) : null}
      <Button variant="ghost" className={size} disabled={busy || editing} onClick={onEdit}>
        Edit
      </Button>
      <Button
        variant="ghost"
        className={`${size} text-red-700 dark:text-red-300`}
        disabled={busy}
        onClick={line.recorded ? () => setConfirming(true) : onDelete}
        aria-label={`Delete line ${line.seq}`}
      >
        Delete
      </Button>
    </div>
  );
}

/** The attributes the table row and the card share: the roving focus and the label. */
function lineAttributes(line: PurchaseLine, current: boolean, onFocus: () => void) {
  return {
    id: `review-line-${line.id}`,
    tabIndex: current ? 0 : -1,
    "aria-label": `Line ${line.seq}: ${lineTitle(line)}`,
    "data-testid": "review-line",
    "data-quiet": isQuietLine(line) ? "true" : undefined,
    // The line itself taking focus makes it current, not a control inside it:
    // Tabbing onto a compact line's Open would otherwise open the line and
    // replace the very button that had focus.
    onFocus: (event: FocusEvent<HTMLElement>) => {
      if (event.target === event.currentTarget) onFocus();
    },
  };
}

function ReviewLine(props: ReviewLineProps) {
  const { line, current, picking, editing, busy, onFocus, onPatch, onCancelEdit } = props;
  const quiet = isQuietLine(line);
  // Draw the eye to flags, suggestions, and unidentified items; not to ignored or automatic lines.
  const attention = needsYou(line);
  // Only the line being worked on opens up; the rest stay one line each (#35).
  const compact = !current && !picking && !editing;
  const pad = compact ? "py-1" : "py-2";

  const row = (
    <tr
      {...lineAttributes(line, current, onFocus)}
      aria-selected={current}
      className={`align-top ${focusRing} ${current ? "bg-neutral-100 dark:bg-neutral-900" : ""} ${quiet ? "text-neutral-600 dark:text-neutral-400" : ""} ${attention ? "border-l-4 border-l-amber-400" : "border-l-4 border-l-transparent"}`}
    >
      <td className={`${pad} pr-2 pl-1`}>
        <LineTags line={line} />
      </td>
      {/* max-w-0 + w-full: this column takes what the others leave and the
          compact text truncates inside it. Without it each truncated (nowrap)
          line set the column's minimum, and Product and Actions scrolled away. */}
      <td className={`${pad} pr-2 w-full max-w-0`}>
        <LineRaw line={line} compact={compact} />
      </td>
      <td className={`${pad} pr-2 whitespace-nowrap`}>
        {editing ? (
          <LineEditor line={line} busy={busy} onPatch={onPatch} onCancel={onCancelEdit} />
        ) : (
          <>
            <LineParsed line={line} compact={compact} />
            {compact ? null : (
              <>
                <DecimalFix line={line} busy={busy} onPatch={onPatch} />
                <QuantityMerge line={line} mergeInto={props.mergeInto} busy={busy} onMerge={props.onMerge} />
              </>
            )}
          </>
        )}
      </td>
      <td className={`${pad} pr-2`}>
        {picking ? <span className={hintClass}>Choosing below</span> : <LineProduct {...props} compact={compact} />}
      </td>
      <td className={pad}>
        {compact ? (
          // Not the shared Button: its desktop minimum height would set every
          // compact row's height, which is the thing #35 is about.
          <button
            type="button"
            // Focus moves to the line, which stays in the document and becomes
            // current, so keyboard focus is never dropped.
            onClick={(event) => event.currentTarget.closest<HTMLElement>("[data-testid=review-line]")?.focus()}
            aria-label={`Open line ${line.seq}`}
            className={`inline-flex min-h-11 items-center rounded-md px-2 text-xs text-neutral-700 hover:bg-neutral-200 lg:min-h-6 dark:text-neutral-300 dark:hover:bg-neutral-800 ${focusRing}`}
          >
            Open
          </button>
        ) : (
          <LineActions {...props} />
        )}
      </td>
    </tr>
  );
  if (!picking) return row;
  // The picker gets a row of its own, the full width of the table, under the
  // line it is for. In the Product cell it made the table wider than its column
  // at every desktop width, and the receipt text scrolled or squeezed out of
  // view while the line was being identified (#61).
  return (
    <>
      {row}
      <tr data-testid="review-pick-row" className={`bg-neutral-100 dark:bg-neutral-900 ${attention ? "border-l-4 border-l-amber-400" : "border-l-4 border-l-transparent"}`}>
        <td colSpan={5} className="pb-3 pl-1 pr-2">
          <div className="max-w-xl">
            <LineProduct {...props} compact={false} />
          </div>
        </td>
      </tr>
    </>
  );
}

/** One line as a card, below lg (G18): the receipt text, what it was read as, then the product. */
function ReviewCard(props: ReviewLineProps) {
  const { line, current, editing, busy, onFocus, onPatch, onCancelEdit } = props;
  const quiet = isQuietLine(line);
  const attention = needsYou(line);
  return (
    <li
      {...lineAttributes(line, current, onFocus)}
      aria-current={current ? "true" : undefined}
      className={`flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-3 text-sm dark:border-neutral-800 dark:bg-neutral-900 ${focusRing} ${quiet ? "text-neutral-600 dark:text-neutral-400" : ""} ${
        attention ? "border-l-4 border-l-amber-400" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <LineRaw line={line} />
        </div>
        <div className="shrink-0 text-right text-xs text-neutral-600 dark:text-neutral-400">
          <LineTags line={line} />
        </div>
      </div>
      <div>
        {editing ? (
          <LineEditor line={line} busy={busy} onPatch={onPatch} onCancel={onCancelEdit} />
        ) : (
          <>
            <LineParsed line={line} />
            <DecimalFix line={line} busy={busy} onPatch={onPatch} />
            <QuantityMerge line={line} mergeInto={props.mergeInto} busy={busy} onMerge={props.onMerge} />
          </>
        )}
      </div>
      <div>
        <LineProduct {...props} touch />
      </div>
      <LineActions {...props} touch />
    </li>
  );
}

function LineEditor({ line, busy, onPatch, onCancel }: { line: PurchaseLine; busy: boolean; onPatch: (input: LinePatchInput) => void; onCancel: () => void }) {
  const [qty, setQty] = useState(line.qty ?? "");
  const [unit, setUnit] = useState(line.unit ?? "");
  const [unitPrice, setUnitPrice] = useState(editableMoney(line.unit_price));
  const [total, setTotal] = useState(editableMoney(line.line_total));
  const [kind, setKind] = useState(line.line_kind);
  const [invalid, setInvalid] = useState<string | null>(null);
  const base = `review-edit-${line.id}`;

  const save = () => {
    const input: LinePatchInput = {};
    if (qty.trim() === "" && line.qty !== null) input.clear_qty = true;
    else if (qty.trim() !== "") {
      if (!isPositiveDecimal(qty)) return setInvalid("Quantity must be a positive number.");
      if (qty.trim() !== line.qty) input.qty = qty.trim();
      if (unit !== (line.unit ?? "")) input.unit = unit;
    }
    if (unitPrice.trim() !== "" && !sameAmount(unitPrice.trim(), line.unit_price)) {
      if (!isNonNegativeDecimal(unitPrice)) return setInvalid("Unit price must be a number.");
      input.unit_price = unitPrice.trim();
    }
    // Saving a flagged price as it stands confirms it, which is what clears the
    // warning (the server clears it only when it is sent).
    const confirmsFlaggedPrice = line.flags.some((f) => PRICE_FLAGS.includes(f));
    if (total.trim() !== "" && (confirmsFlaggedPrice || !sameAmount(total.trim(), line.line_total))) {
      if (!isNonNegativeDecimal(total)) return setInvalid("Line total must be a number.");
      input.line_total = total.trim();
    }
    if (kind !== line.line_kind) input.line_kind = kind;
    setInvalid(null);
    if (Object.keys(input).length === 0) return onCancel();
    onPatch(input);
  };

  return (
    <div
      className="flex flex-col gap-2"
      role="group"
      aria-label={`Edit line ${line.seq}`}
      onKeyDown={(e) => {
        if (e.key === "Enter" && (e.target as HTMLElement).tagName !== "BUTTON") {
          e.preventDefault();
          e.stopPropagation();
          save();
        }
      }}
    >
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      <div className="grid grid-cols-2 gap-2">
        <Field id={`${base}-qty`} label="Qty" inputMode="decimal" autoComplete="off" value={qty} onChange={(e) => setQty(e.target.value)} className="text-xs" />
        <UnitSelect id={`${base}-unit`} label="Unit" value={unit} onChange={setUnit} />
        <Field id={`${base}-unit-price`} label="Unit price" inputMode="decimal" autoComplete="off" value={unitPrice} onChange={(e) => setUnitPrice(e.target.value)} />
        <Field id={`${base}-total`} label="Line total" inputMode="decimal" autoComplete="off" value={total} onChange={(e) => setTotal(e.target.value)} />
        <SelectField id={`${base}-kind`} label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          {LINE_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
          {LINE_KINDS.includes(kind as (typeof LINE_KINDS)[number]) ? null : <option value={kind}>{kind}</option>}
        </SelectField>
      </div>
      <div className="flex gap-1">
        <Button className="min-h-11 lg:min-h-8 px-2 text-xs" disabled={busy} onClick={save}>
          Save line
        </Button>
        <Button variant="secondary" className="min-h-11 lg:min-h-8 px-2 text-xs" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function AddLineForm({ itemLines, busy, onAdd }: { itemLines: PurchaseLine[]; busy: boolean; onAdd: (input: LineAddInput) => void }) {
  const [rawText, setRawText] = useState("");
  const [kind, setKind] = useState<string>("item");
  const [qty, setQty] = useState("");
  const [unit, setUnit] = useState("");
  const [total, setTotal] = useState("");
  const [parent, setParent] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const add = () => {
    if (total.trim() === "" || !isNonNegativeDecimal(total)) return setInvalid("A line total is required.");
    if (qty.trim() !== "" && !isPositiveDecimal(qty)) return setInvalid("Quantity must be a positive number.");
    setInvalid(null);
    const input: LineAddInput = { line_kind: kind, line_total: total.trim() };
    if (rawText.trim()) input.raw_text = rawText.trim();
    if (qty.trim()) {
      input.qty = qty.trim();
      if (unit) input.unit = unit;
    }
    if (parent && ATTACHABLE.has(kind)) input.parent_line_id = parent;
    onAdd(input);
    setRawText("");
    setQty("");
    setTotal("");
  };

  return (
    <div
      className="flex flex-col gap-3"
      role="group"
      aria-label="New line"
      onKeyDown={(e) => {
        if (e.key === "Enter" && (e.target as HTMLElement).tagName !== "BUTTON") {
          e.preventDefault();
          e.stopPropagation();
          add();
        }
      }}
    >
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id="add-line-text" label="Receipt text" autoComplete="off" value={rawText} onChange={(e) => setRawText(e.target.value)} />
        <SelectField id="add-line-kind" label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          {LINE_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </SelectField>
        <Field id="add-line-total" label="Line total" inputMode="decimal" autoComplete="off" required value={total} onChange={(e) => setTotal(e.target.value)} />
        <Field id="add-line-qty" label="Quantity" inputMode="decimal" autoComplete="off" value={qty} onChange={(e) => setQty(e.target.value)} />
        <UnitSelect id="add-line-unit" label="Unit" value={unit} onChange={setUnit} />
        {ATTACHABLE.has(kind) ? (
          <SelectField id="add-line-parent" label="Attach to" value={parent} onChange={(e) => setParent(e.target.value)}>
            <option value="">nothing</option>
            {itemLines.map((i) => (
              <option key={i.id} value={i.id}>
                #{i.seq} {lineTitle(i)}
              </option>
            ))}
          </SelectField>
        ) : null}
      </div>
      <div>
        <Button variant="secondary" disabled={busy} onClick={add}>
          Add line
        </Button>
      </div>
    </div>
  );
}

/** The line's best-by date, and a field for the date printed on its label (2Q). */
function ReviewBestBy({ line }: { line: PurchaseLine }) {
  const { id = "" } = useParams<{ id: string }>();
  return (
    <div className="mt-1 text-sm">
      <BestBy purchaseId={id} line={line} inReview />
    </div>
  );
}

/** "Remember {code} for {product}" (04, 2K): nothing is recorded without this click. */
function RememberCode({ line }: { line: PurchaseLine }) {
  const { id = "" } = useParams<{ id: string }>();
  const remember = useRememberCode(id);
  if (!line.code_offer || !line.product) return null;
  return (
    <span className="mt-1 flex flex-wrap items-center gap-2 text-xs">
      <Button variant="secondary" className="min-h-11 px-2 text-xs lg:min-h-8" disabled={remember.isPending} onClick={() => remember.mutate(line.id)}>
        Remember {line.code_offer.value} for {productTitle(line.product)}
      </Button>
      {remember.isError ? <span className="text-red-800 dark:text-red-300">{errorMessage(remember.error)}</span> : null}
    </span>
  );
}
