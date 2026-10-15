import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import { usePurchase } from "../../api/purchases";
import {
  rereadReason,
  useApplyReread,
  useDismissReread,
  useLatestReread,
  useRequestReread,
  type Reread,
  type RereadChange,
  type RereadField,
  type RereadLine,
  type RereadMode,
} from "../../api/rereads";
import { useNavigateWithNotice } from "../../components/Notice";
import { flagLabel } from "../../components/purchases/lineParts";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  focusRing,
  PageHeader,
  sectionHeading,
} from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

const hintClass = "text-sm text-neutral-600 dark:text-neutral-400";

const FIELD_LABELS: Record<RereadField, string> = {
  raw_text: "Receipt text",
  line_kind: "Kind",
  qty: "Quantity",
  unit: "Unit",
  unit_price: "Price each",
  line_total: "Amount",
  attached: "Belongs to",
  flags: "Notes",
};

const KIND_LABELS: Record<string, string> = {
  item: "Item",
  discount: "Discount",
  tax: "Tax",
  deposit: "Deposit",
  fee: "Fee",
};

const KEPT_WHY = {
  edited: "You edited this line",
  added: "You added this line",
  removed: "You removed this line",
} as const;

function show(line: RereadLine, field: RereadField): string {
  const value = line[field];
  if (field === "flags") return (value as string[]).map(flagLabel).join(", ") || "none";
  if (value === null || value === "") return "none";
  if (field === "line_total" || field === "unit_price") return formatMoney(value as string, 2, 4);
  if (field === "line_kind") return KIND_LABELS[value as string] ?? (value as string);
  return value as string;
}

/** A line read as a whole: what it is and what it comes to. */
function summary(line: RereadLine): string {
  const qty = line.qty
    ? `${line.qty}${line.unit && line.unit !== "each" ? ` ${line.unit}` : ""} · `
    : "";
  return `${KIND_LABELS[line.line_kind] ?? line.line_kind} · ${qty}${formatMoney(line.line_total)}`;
}

function Change({ change }: { change: RereadChange }) {
  const line = change.after ?? change.before ?? change.reading;
  const text = line?.raw_text ?? "";
  const label = {
    changed: "Changed",
    added: "Added",
    removed: "Removed",
    kept: "Kept as you left it",
  }[change.action];
  const quiet = change.action === "kept";
  return (
    <li
      className={`flex min-w-0 flex-col gap-1 border-b border-neutral-200 py-3 last:border-b-0 dark:border-neutral-800 ${quiet ? "text-neutral-600 dark:text-neutral-400" : ""}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-xs font-semibold">{label}</span>
        <span className="min-w-0 break-words font-mono text-sm">{text}</span>
      </div>
      {change.action === "changed" && change.before && change.after ? (
        <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
          {(change.fields ?? []).map((field) => (
            <div key={field} className="contents">
              <dt className="text-neutral-600 dark:text-neutral-400">{FIELD_LABELS[field]}</dt>
              <dd className="min-w-0 break-words">
                <span className="sr-only">was </span>
                <s className="text-neutral-600 dark:text-neutral-400">
                  {show(change.before!, field)}
                </s>
                <span aria-hidden="true"> → </span>
                <span className="sr-only">, now </span>
                <span>{show(change.after!, field)}</span>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {change.action === "added" && change.after ? (
        <p className="text-sm">{summary(change.after)}</p>
      ) : null}
      {change.action === "removed" && change.before ? (
        <p className="text-sm">{summary(change.before)}</p>
      ) : null}
      {change.action === "kept" && change.why ? (
        <p className="text-sm">{KEPT_WHY[change.why]}. Today's reading differs; yours stays.</p>
      ) : null}
    </li>
  );
}

function money(value: string | null): string {
  return formatMoney((value ?? "0").replace(/^-/, ""));
}

function Summary({ reread }: { reread: Reread }) {
  const p = reread.proposal!;
  const after = p.after.reconciliation;
  const before = p.before.reconciliation;
  const counts = [
    p.counts.changed ? `${p.counts.changed} changed` : null,
    p.counts.added ? `${p.counts.added} added` : null,
    p.counts.removed ? `${p.counts.removed} removed` : null,
    p.counts.kept ? `${p.counts.kept} kept as you left them` : null,
  ].filter(Boolean);
  return (
    <div className="flex flex-col gap-1">
      {after.checked ? (
        after.mismatch ? (
          <p className="text-sm">
            Off by {money(after.difference)} now
            {before.mismatch ? ` (was ${money(before.difference)})` : " (adds up as it is)"}.
          </p>
        ) : (
          <p className="text-sm font-medium text-green-700 dark:text-green-400">
            Adds up now.{before.mismatch ? ` It was off by ${money(before.difference)}.` : ""}
          </p>
        )
      ) : null}
      <p className={hintClass}>{counts.join(", ")}.</p>
    </div>
  );
}

export function RereadPage() {
  const { id = "" } = useParams<{ id: string }>();
  const purchase = usePurchase(id);
  const latest = useLatestReread(id);
  const request = useRequestReread(id);
  const apply = useApplyReread(id);
  const dismiss = useDismissReread(id);
  const navigateWithNotice = useNavigateWithNotice();
  usePageTitle("Re-read lines");

  const back = `/shop/purchases/${id}`;
  const backLink = (
    <Link
      to={back}
      className={`inline-flex min-h-11 items-center rounded-md underline lg:min-h-10 ${focusRing}`}
    >
      {purchase.data ? `Receipt, ${formatDate(purchase.data.purchased_at)}` : "Back to the receipt"}
    </Link>
  );

  if (purchase.isPending || latest.isPending) {
    return (
      <p role="status" className={hintClass}>
        Loading…
      </p>
    );
  }
  if (purchase.isError) return <Alert tone="error">{errorMessage(purchase.error)}</Alert>;
  if (latest.isError) return <Alert tone="error">{errorMessage(latest.error)}</Alert>;

  const reread = latest.data;
  const committed = purchase.data.status === "committed";
  const canRecheck = Boolean(purchase.data.reread?.can_recheck);
  const busy = request.isPending || apply.isPending || dismiss.isPending;
  const stale = apply.isError && isApiError(apply.error) && apply.error.code === "reread_stale";

  const again = (mode: RereadMode) => {
    const start = () => request.mutate(mode, { onSuccess: () => apply.reset() });
    if (reread && ["pending", "running", "ready"].includes(reread.status))
      dismiss.mutate(reread.id, { onSuccess: start });
    else start();
  };
  const againButtons = (
    <span className="flex flex-wrap gap-2">
      {canRecheck ? (
        <Button variant="secondary" disabled={busy} onClick={() => again("check")}>
          Re-check the lines
        </Button>
      ) : null}
      <Button variant="secondary" disabled={busy} onClick={() => again("read")}>
        Read again
      </Button>
    </span>
  );

  const header = (actions?: ReactNode) => (
    <>
      <nav
        aria-label="Breadcrumb"
        className={`mb-2 flex flex-wrap items-center gap-1 ${hintClass}`}
      >
        <Link to="/shop/purchases" className={`rounded-md underline ${focusRing}`}>
          Purchases
        </Link>
        <span aria-hidden="true">/</span>
        {backLink}
      </nav>
      <PageHeader
        title="Re-read lines"
        description="What today's reading would change. Nothing changes until you apply it."
      >
        {actions}
      </PageHeader>
    </>
  );

  if (!reread || reread.status === "applied" || reread.status === "dismissed") {
    return (
      <>
        {header()}
        <EmptyState title="No re-read is open for this receipt.">{againButtons}</EmptyState>
      </>
    );
  }

  if (reread.status === "pending" || reread.status === "running") {
    return (
      <>
        {header()}
        <Card>
          <p role="status" className="text-sm">
            Reading the receipt again… This page updates when it's done.
          </p>
        </Card>
      </>
    );
  }

  if (reread.status === "failed") {
    return (
      <>
        {header()}
        <div className="flex flex-col gap-3">
          <Alert tone="error">{rereadReason(reread.reason)}</Alert>
          <span>
            <Button variant="secondary" disabled={busy} onClick={() => again("read")}>
              Try again
            </Button>
          </span>
        </div>
      </>
    );
  }

  if (reread.status === "no_changes") {
    return (
      <>
        {header()}
        <EmptyState title="Today's reading matches these lines.">{backLink}</EmptyState>
      </>
    );
  }

  const p = reread.proposal!;
  const changed = p.counts.changed + p.counts.added + p.counts.removed;
  const onApply = () =>
    apply.mutate(reread.id, {
      onSuccess: () =>
        navigateWithNotice(back, {
          tone: "success",
          message: `Re-read applied: ${changed} ${changed === 1 ? "line" : "lines"} changed.${committed ? " Commit to update its prices." : ""}`,
        }),
    });
  const onDiscard = () =>
    dismiss.mutate(reread.id, {
      onSuccess: () => navigateWithNotice(back, { tone: "success", message: "Re-read discarded." }),
    });
  const actions = (
    <>
      <Button disabled={busy || stale} onClick={onApply}>
        {apply.isPending ? "Applying…" : "Apply changes"}
      </Button>
      <Button variant="secondary" disabled={busy} onClick={onDiscard}>
        Discard
      </Button>
    </>
  );

  return (
    <>
      {header(<span className="hidden flex-wrap gap-2 lg:flex">{actions}</span>)}
      <div className="flex flex-col gap-4">
        {stale ? (
          <Alert tone="warn">
            <p>
              The lines changed since this re-read. Re-check or read again to see what would change
              now.
            </p>
            <div className="mt-2">{againButtons}</div>
          </Alert>
        ) : apply.isError ? (
          <Alert tone="error">{errorMessage(apply.error)}</Alert>
        ) : null}
        {dismiss.isError ? <Alert tone="error">{errorMessage(dismiss.error)}</Alert> : null}
        {committed ? (
          <p className={hintClass}>
            Applying reopens this purchase. Its prices change when you commit it again.
          </p>
        ) : null}
        <Card>
          <Summary reread={reread} />
        </Card>
        <Card>
          <h2 className={`${sectionHeading} mb-1`}>Changes</h2>
          <ol aria-label="Changes" className="flex flex-col">
            {p.changes.map((change, at) => (
              <Change key={change.line_id ?? `new-${at}`} change={change} />
            ))}
          </ol>
        </Card>
        {/* Below lg, Apply sits in the thumb zone just above the tab bar (G18). */}
        <div className="sticky bottom-[calc(6rem+env(safe-area-inset-bottom))] z-10 flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 bg-neutral-50/95 p-3 shadow-card backdrop-blur lg:hidden dark:border-neutral-800 dark:bg-neutral-950/95">
          {actions}
        </div>
      </div>
    </>
  );
}
