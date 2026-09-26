import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router";
import { isPositiveDecimal, productTitle, trimDecimal } from "../../api/catalog";
import { useLocations } from "../../api/geo";
import { locationCandidates, receiptImageUrl, useIngestJob, useIngestJobs } from "../../api/ingest";
import {
  LINE_KINDS,
  isQuietLine,
  purchaseErrorMessage,
  resolutionLabel,
  useAddLine,
  fetchNextDraft,
  useCommitPurchase,
  useDeleteLine,
  usePatchLine,
  usePatchPurchase,
  useReResolveLine,
  useResolveLine,
  type AcceptedKind,
  type LineAddInput,
  type LinePatchInput,
  type Purchase,
  type PurchaseHeaderInput,
  type PurchaseLine,
  type Resolution,
  type Suggestion,
} from "../../api/purchases";
import { formatMoney, isNonNegativeDecimal } from "../../lib/decimal";
import { LG_QUERY, useMediaQuery } from "../../lib/useMediaQuery";
import { fromDateTimeLocal, toDateTimeLocal } from "../../lib/openingHours";
import { Badge, Disclosure, SelectField, hintClass } from "../catalog/fields";
import { UnitSelect } from "../catalog/UnitSelect";
import { Alert, Button, Card, Field, focusRing, tapTarget } from "../ui";
import { ProductPicker } from "./ProductPicker";
import { CategoryChip } from "../CategoryChip";
import { useNotice } from "../Notice";
import { SegmentedControl } from "../SegmentedControl";

const acceptedKindOf: Record<Suggestion["kind"], AcceptedKind> = { alias_unconfirmed: "alias", fuzzy: "fuzzy", llm: "llm" };

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
  const id = purchase.id;
  const resolve = useResolveLine(id);
  const reResolve = useReResolveLine(id);
  const patchLine = usePatchLine(id);
  const addLine = useAddLine(id);
  const deleteLine = useDeleteLine(id);
  const commit = useCommitPurchase(id);
  const mutations = [resolve, reResolve, patchLine, addLine, deleteLine, commit];
  const busy = mutations.some((m) => m.isPending);
  const error = mutations.find((m) => m.error)?.error;

  const lines = useMemo(() => [...purchase.lines].sort((a, b) => a.seq - b.seq), [purchase.lines]);
  const itemLines = lines.filter((l) => l.line_kind === "item");
  const [currentId, setCurrentId] = useState<string | null>(() => (lines.find((l) => !isQuietLine(l)) ?? lines[0])?.id ?? null);
  const [picking, setPicking] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
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
  const shown = useMemo(() => {
    const visible = filter === "needs" ? lines.filter(needsYou) : lines;
    return wide ? visible : [...visible.filter(needsYou), ...visible.filter((l) => !needsYou(l))];
  }, [lines, filter, wide]);

  // The page stays and turns Committed, with how many prices the commit added and,
  // when drafts remain, a way to the next one (G9).
  const announceCommit = async (committed: Purchase) => {
    // Only the prices this commit made. A recommit keeps the observations of
    // lines that did not change, and those were in the price book already.
    const before = new Set(purchase.lines.map((l) => l.observation_id).filter(Boolean));
    const n = committed.lines.filter((l) => l.observation_id && !before.has(l.observation_id)).length;
    const message =
      n === 0
        ? "Committed. Its prices were already in the price book."
        : `Committed. ${n} ${n === 1 ? "price" : "prices"} added to the price book.`;
    // Said at once. "Next draft" joins it when the lookup answers, and only if
    // this notice is still showing: a reopen in the meantime dismisses it.
    const id = notice.show({ tone: "success", message });
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

  const lineProps = (line: PurchaseLine): ReviewLineProps => ({
    line,
    itemLines,
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
  });

  const unresolved = itemLines.filter((l) => l.resolution === "unmatched" || !l.product).length;
  const mismatch = purchase.flags.some((f) => f === "reconcile_mismatch" || f === "total_mismatch");

  return (
    <div onKeyDown={onKeyDown} className="flex flex-col gap-4" data-testid="review">
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
      {mismatch ? (
        <Alert tone="info">
          The receipt says {purchase.total !== null ? formatMoney(purchase.total) : "—"} but the lines add up to{" "}
          {purchase.computed_total !== null ? formatMoney(purchase.computed_total) : "—"}. Check the lines or the header.
        </Alert>
      ) : null}

      <div className={`grid gap-4 ${purchase.receipt_document_id ? "md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]" : ""}`}>
        {purchase.receipt_document_id ? (
          <Disclosure summary="Receipt image" defaultOpen className="md:sticky md:top-4 md:self-start">
            <img
              src={receiptImageUrl(purchase.receipt_document_id)}
              alt="The receipt as photographed"
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
              <span className={hintClass}>
                {itemLines.length} items · {unresolved} to identify
              </span>
            </div>
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
            <Disclosure summary="Add a line" className="mt-3">
              <AddLineForm itemLines={itemLines} busy={busy} onAdd={(input) => addLine.mutate(input)} />
            </Disclosure>
          </Card>

          {/* Below lg, Commit sits in the thumb zone just above the tab bar (G18). */}
          <div className="sticky bottom-[calc(6rem+env(safe-area-inset-bottom))] z-10 flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 bg-neutral-50/95 p-3 shadow-sm backdrop-blur lg:static lg:border-0 lg:shadow-none lg:bg-transparent lg:p-0 lg:backdrop-blur-none dark:border-neutral-800 dark:bg-neutral-950/95 lg:dark:bg-transparent">
            <Button onClick={() => setConfirming(true)} disabled={busy} className="min-h-12 flex-1 text-base lg:min-h-10 lg:flex-none lg:text-sm">
              Commit purchase
            </Button>
            <span className={hintClass}>
              {unresolved > 0 ? `${unresolved} unidentified ${unresolved === 1 ? "line" : "lines"} will go to the to-identify queue.` : "Every item line has a product."}
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
              {itemLines.length - unresolved} {itemLines.length - unresolved === 1 ? "line emits" : "lines emit"} a price observation now.
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

  const [form, setForm] = useState({
    vendor_location_id: purchase.vendor_location?.id ?? "",
    purchased_at: toDateTimeLocal(new Date(purchase.purchased_at)),
    subtotal: purchase.subtotal ?? "",
    tax: purchase.tax ?? "",
    total: purchase.total ?? "",
  });
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }));

  const save = () => {
    const input: PurchaseHeaderInput = {};
    if (form.vendor_location_id && form.vendor_location_id !== (purchase.vendor_location?.id ?? "")) input.vendor_location_id = form.vendor_location_id;
    const at = fromDateTimeLocal(form.purchased_at);
    if (!at) return setInvalid("Enter a valid date and time.");
    if (form.purchased_at !== toDateTimeLocal(new Date(purchase.purchased_at))) input.purchased_at = at;
    for (const key of ["subtotal", "tax", "total"] as const) {
      const value = form[key].trim();
      if (value !== "" && !isNonNegativeDecimal(value)) return setInvalid(`${key[0].toUpperCase()}${key.slice(1)} must be a number.`);
      if (value !== "" && value !== (purchase[key] ?? "")) input[key] = value;
    }
    setInvalid(null);
    if (Object.keys(input).length > 0) patch.mutate(input);
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
                >
                  {c.vendor_name === c.name ? c.name : `${c.vendor_name} — ${c.name}`} · {c.score}
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
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id="review-subtotal" label="Subtotal" inputMode="decimal" autoComplete="off" value={form.subtotal} onChange={(e) => set("subtotal", e.target.value)} disabled={patch.isPending} />
        <Field id="review-tax" label="Tax" inputMode="decimal" autoComplete="off" value={form.tax} onChange={(e) => set("tax", e.target.value)} disabled={patch.isPending} />
        <Field id="review-total" label="Total" inputMode="decimal" autoComplete="off" value={form.total} onChange={(e) => set("total", e.target.value)} disabled={patch.isPending} />
      </div>
      <div>
        <Button variant="secondary" onClick={save} disabled={patch.isPending}>
          {patch.isPending ? "Saving…" : "Save header"}
        </Button>
      </div>
    </div>
  );
}

// --- lines ------------------------------------------------------------------

interface ReviewLineProps {
  line: PurchaseLine;
  itemLines: PurchaseLine[];
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
          <Badge tone="warn">{f.replaceAll("_", " ")}</Badge>
        </span>
      ))}
    </>
  );
}

/** What the receipt says, verbatim and normalized. */
function LineRaw({ line }: { line: PurchaseLine }) {
  return (
    <>
      <span className="font-mono text-xs break-all">{line.raw_text ?? "—"}</span>
      {line.raw_text_norm && line.raw_text_norm !== line.raw_text ? (
        <span className="block text-[11px] text-neutral-600 dark:text-neutral-400">{line.raw_text_norm}</span>
      ) : null}
    </>
  );
}

/** What it was read as: quantity × unit, and the prices. */
function LineParsed({ line }: { line: PurchaseLine }) {
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
function LineProduct({ line, itemLines, picking, busy, onAccept, onClosePicker, onChoose, onPatch, touch }: LinePartProps & { touch?: boolean }) {
  const quiet = isQuietLine(line);
  const isItem = line.line_kind === "item";
  const suggestions = line.suggestions ?? [];
  const top = topSuggestion(line);
  const resolution = line.resolution as Resolution | null;

  if (picking) {
    return (
      <div className="flex flex-col gap-1">
        <ProductPicker id={`review-pick-${line.id}`} label={`Product for line ${line.seq}`} hideLabel value={null} onChange={(p) => p && onChoose(p.id)} disabled={busy} />
        <Button variant="ghost" className="min-h-11 lg:min-h-8 self-start px-2 text-xs" onClick={onClosePicker}>
          Cancel
        </Button>
      </div>
    );
  }

  const kindLabel = (s: Suggestion) => (s.kind === "alias_unconfirmed" ? "alias" : s.kind === "llm" ? "model" : "fuzzy");

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
      <Button variant="ghost" className={`${size} text-red-700 dark:text-red-300`} disabled={busy} onClick={onDelete} aria-label={`Delete line ${line.seq}`}>
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
    onFocus,
  };
}

function ReviewLine(props: ReviewLineProps) {
  const { line, current, editing, busy, onFocus, onPatch, onCancelEdit } = props;
  const quiet = isQuietLine(line);
  // Draw the eye to flags, suggestions, and unidentified items; not to ignored or automatic lines.
  const attention = needsYou(line);

  return (
    <tr
      {...lineAttributes(line, current, onFocus)}
      aria-selected={current}
      className={`align-top ${focusRing} ${current ? "bg-neutral-100 dark:bg-neutral-900" : ""} ${quiet ? "text-neutral-600 dark:text-neutral-400" : ""} ${attention ? "border-l-4 border-l-amber-400" : "border-l-4 border-l-transparent"}`}
    >
      <td className="py-2 pr-2 pl-1">
        <LineTags line={line} />
      </td>
      <td className="py-2 pr-2">
        <LineRaw line={line} />
      </td>
      <td className="py-2 pr-2 whitespace-nowrap">
        {editing ? <LineEditor line={line} busy={busy} onPatch={onPatch} onCancel={onCancelEdit} /> : <LineParsed line={line} />}
      </td>
      <td className="py-2 pr-2">
        <LineProduct {...props} />
      </td>
      <td className="py-2">
        <LineActions {...props} />
      </td>
    </tr>
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
      <div>{editing ? <LineEditor line={line} busy={busy} onPatch={onPatch} onCancel={onCancelEdit} /> : <LineParsed line={line} />}</div>
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
  const [unitPrice, setUnitPrice] = useState(line.unit_price ?? "");
  const [total, setTotal] = useState(line.line_total ?? "");
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
    if (unitPrice.trim() !== "" && unitPrice.trim() !== line.unit_price) {
      if (!isNonNegativeDecimal(unitPrice)) return setInvalid("Unit price must be a number.");
      input.unit_price = unitPrice.trim();
    }
    if (total.trim() !== "" && total.trim() !== line.line_total) {
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
