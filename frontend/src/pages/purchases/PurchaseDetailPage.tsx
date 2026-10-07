import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { formatPack, productTitle, trimDecimal } from "../../api/catalog";
import { errorMessage, isApiError } from "../../api/client";
import {
  purchaseErrorMessage,
  purchaseLocationLabel,
  purchaseStatusLabel,
  purchaseStatusTone,
  resolutionLabel,
  sourceLabel,
  usePurchase,
  useReopenPurchase,
  useUpdatePurchase,
  type Purchase,
  type PurchaseLine,
  type RemovedPurchase,
  type Resolution,
} from "../../api/purchases";
import { Badge } from "../../components/catalog/fields";
import { PurchaseForm, isLineEmpty, purchaseValues } from "../../components/purchases/PurchaseForm";
import { RemovedLinesCaption, RemovePurchase, RestorePurchase } from "../../components/purchases/RemovePurchase";
import { ReviewPurchase } from "../../components/purchases/ReviewPurchase";
import { Alert, Button, Card, EmptyState, PageHeader, alertTones, focusRing } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate, formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";
import { CategoryChip } from "../../components/CategoryChip";
import { useNavigateWithNotice, useNotice } from "../../components/Notice";

/**
 * A purchase. Draft and reviewed purchases open in review mode (Phase 2D);
 * committed ones show their lines and observations, with Reopen to go back
 * to review and, for manual purchases, the entry form to edit them outright.
 * Both end with "Remove this purchase" (#74); a voided one is read-only and
 * ends with "Restore this purchase" (issue 210), which takes it back to review.
 */
export function PurchaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const purchase = usePurchase(id);
  const [editing, setEditing] = useState(false);
  const title = purchase.data
    ? `${purchase.data.vendor_location?.vendor.name ?? "Receipt"} · ${formatDateTime(purchase.data.purchased_at)}`
    : "Purchase";
  usePageTitle(title);

  const notice = useNotice();
  const navigateWithNotice = useNavigateWithNotice();
  // After a void the page turns into the voided view; its notice takes focus.
  const [justVoided, setJustVoided] = useState(false);
  const onRemoved = (removed: RemovedPurchase) => {
    if (removed.outcome === "void") {
      setJustVoided(true);
      return;
    }
    const photo = removed.photo_deleted ? " Its receipt photo was deleted." : "";
    navigateWithNotice("/shop/purchases", { tone: "success", message: `Purchase removed.${photo}` }, { replace: true });
  };

  if (purchase.isPending) {
    return (
      <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
        Loading…
      </p>
    );
  }
  if (purchase.isError) {
    // An old link to a purchase that was removed is not an error to report (D9).
    if (isApiError(purchase.error) && purchase.error.status === 404) {
      return (
        <>
          <PageHeader title="Purchase" />
          <EmptyState title="This purchase doesn't exist. It may have been removed.">
            <Link to="/shop/purchases" className={`rounded-md underline ${focusRing}`}>
              Purchases
            </Link>
          </EmptyState>
        </>
      );
    }
    return <Alert tone="error">{errorMessage(purchase.error)}</Alert>;
  }
  const p = purchase.data;

  if (editing) {
    return (
      <>
        <PageHeader title={title} />
        <Card>
          <EditPurchase purchase={p} onDone={() => setEditing(false)} />
        </Card>
      </>
    );
  }

  if (p.status === "voided") {
    return (
      <CommittedPurchase
        purchase={p}
        title={title}
        focusNotice={justVoided}
        onRestored={() => {
          setJustVoided(false);
          notice.show({ tone: "success", message: "Restored. Commit it to put its prices back in the price book." });
        }}
      />
    );
  }

  if (p.status !== "committed") {
    return (
      <>
        <PageHeader title={title}>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={purchaseStatusTone[p.status]}>{purchaseStatusLabel[p.status]}</Badge>
            <span className="text-sm text-neutral-600 dark:text-neutral-400">{sourceLabel[p.source]}</span>
            <Link to="/shop/purchases" className={`inline-flex min-h-11 lg:min-h-10 items-center rounded-md px-2 text-sm underline ${focusRing}`}>
              All purchases
            </Link>
          </div>
        </PageHeader>
        <ReviewPurchase purchase={p} />
        <RemovePurchase purchase={p} onRemoved={onRemoved} />
      </>
    );
  }

  return (
    <CommittedPurchase
      purchase={p}
      title={title}
      // A confirmation about the committed purchase (the first-purchase one, a
      // commit) no longer holds once it is reopened.
      onReopened={notice.dismiss}
      onEdit={() => setEditing(true)}
      onRemoved={onRemoved}
    />
  );
}

/** "Removed {date} by {name}. Its {n} prices no longer count in the price book." (D4) */
function VoidedNotice({ purchase: p, focus }: { purchase: Purchase; focus: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focus) ref.current?.focus();
  }, [focus]);
  // What the removal itself voided; prices voided before it are not counted twice.
  const n = p.voided_prices ?? 0;
  const when = p.voided_at ? formatDate(p.voided_at) : "";
  const who = p.voided_by_name ? ` by ${p.voided_by_name}` : "";
  return (
    <div ref={ref} tabIndex={-1} role="status" className={`rounded-md border px-3 py-2 text-sm ${alertTones.info} ${focusRing}`}>
      Removed {when}
      {who}.{" "}
      {n === 0
        ? // Its prices were voided before the removal (a reopen, a removed line).
          "None of its prices were still counting in the price book."
        : `Its ${n} ${n === 1 ? "price no longer counts" : "prices no longer count"} in the price book.`}
    </div>
  );
}


/** A committed purchase, or, without the actions but Restore, a voided one (D4). */
function CommittedPurchase({
  purchase: p,
  title,
  onReopened,
  onEdit,
  onRemoved,
  onRestored,
  focusNotice = false,
}: {
  purchase: Purchase;
  title: string;
  onReopened?: () => void;
  onEdit?: () => void;
  onRemoved?: (removed: RemovedPurchase) => void;
  onRestored?: () => void;
  focusNotice?: boolean;
}) {
  const reopen = useReopenPurchase(p.id);
  const voided = p.status === "voided";
  return (
    <>
      <PageHeader title={title}>
        <div className="flex flex-wrap gap-2">
          {!voided && p.source === "manual" ? (
            <Button variant="secondary" onClick={onEdit}>
              Edit
            </Button>
          ) : null}
          {!voided ? (
            <Button variant="secondary" disabled={reopen.isPending} onClick={() => reopen.mutate(undefined, { onSuccess: onReopened })}>
              {reopen.isPending ? "Reopening…" : "Reopen"}
            </Button>
          ) : null}
          <Link to="/shop/purchases" className={`inline-flex min-h-11 lg:min-h-10 items-center rounded-md px-2 text-sm underline ${focusRing}`}>
            All purchases
          </Link>
        </div>
      </PageHeader>

      <div className="flex flex-col gap-6">
        {voided ? <VoidedNotice purchase={p} focus={focusNotice} /> : null}
        {reopen.error ? <Alert tone="error">{purchaseErrorMessage(reopen.error)}</Alert> : null}
        <Card>
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <Item label="Location">
              {p.vendor_location ? (
                <>
                  <Link to={`/catalog/vendors/${p.vendor_location.vendor.id}`} className={`rounded underline ${focusRing}`}>
                    {p.vendor_location.vendor.name}
                  </Link>
                  {p.vendor_location.name !== p.vendor_location.vendor.name ? ` — ${p.vendor_location.name}` : ""}
                </>
              ) : (
                purchaseLocationLabel(p)
              )}
            </Item>
            <Item label="Purchased">{formatDateTime(p.purchased_at)}</Item>
            <Item label="Status">
              <Badge tone={purchaseStatusTone[p.status]}>{purchaseStatusLabel[p.status]}</Badge> <span>{sourceLabel[p.source]}</span>
            </Item>
            <Item label="Total">{p.total !== null ? formatMoney(p.total) : "—"}</Item>
            <Item label="Computed total">{p.computed_total !== null ? formatMoney(p.computed_total) : "—"}</Item>
            <Item label="Tax">{p.tax !== null ? formatMoney(p.tax) : "—"}</Item>
            {p.flags.length > 0 ? (
              <Item label="Flags">
                <span className="flex flex-wrap gap-1">
                  {p.flags.map((f) => (
                    <Badge key={f} tone="warn">
                      {f.replaceAll("_", " ")}
                    </Badge>
                  ))}
                </span>
              </Item>
            ) : null}
          </dl>
        </Card>

        <Card>
          <h2 className="mb-3 text-lg font-medium">Lines</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-label="Lines">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                  <th className="py-2 pr-3">#</th>
                  <th className="py-2 pr-3">Product</th>
                  <th className="py-2 pr-3 text-right">Qty</th>
                  <th className="py-2 pr-3 text-right">Unit price</th>
                  <th className="py-2 pr-3 text-right">Total</th>
                  <th className="py-2 pr-3">Flags</th>
                  <th className="py-2">Observation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                {p.lines.map((l) => (
                  <LineRow key={l.id} line={l} voided={voided} />
                ))}
              </tbody>
            </table>
          </div>
          <RemovedLinesCaption purchase={p} />
          {p.lines.some((l) => l.line_kind === "item" && l.resolution === "unmatched") ? (
            <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
              Unidentified lines wait in the{" "}
              <Link to="/shop/receipts/identify" className={`rounded underline ${focusRing}`}>
                to-identify queue
              </Link>
              .
            </p>
          ) : null}
        </Card>
        {onRemoved ? <RemovePurchase purchase={p} onRemoved={onRemoved} /> : null}
        {voided && onRestored ? <RestorePurchase purchase={p} onRestored={onRestored} /> : null}
      </div>
    </>
  );
}

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-neutral-600 dark:text-neutral-400">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function LineRow({ line, voided = false }: { line: PurchaseLine; voided?: boolean }) {
  const isItem = line.line_kind === "item";
  const resolution = line.resolution as Resolution | null;
  return (
    <tr className={isItem ? "" : "text-neutral-600 dark:text-neutral-400"}>
      <td className="py-2 pr-3 tabular-nums">{line.seq}</td>
      <td className="py-2 pr-3">
        {line.product ? (
          <>
            <Link to={`/catalog/products/${line.product.id}`} className={`rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
              {productTitle(line.product)}
            </Link>
            {formatPack(line.product.pack_qty, line.product.pack_unit) ? (
              <span className="text-neutral-600 dark:text-neutral-400"> · {formatPack(line.product.pack_qty, line.product.pack_unit)}</span>
            ) : null}{" "}
            <CategoryChip category={line.product.category} categoryKey={line.product.category_key} />
          </>
        ) : (
          <span>
            {line.raw_text ?? "—"}{" "}
            {isItem ? (
              resolution === "ignored" ? (
                <Badge>ignored</Badge>
              ) : (
                <Badge tone="warn">unresolved</Badge>
              )
            ) : (
              <Badge>{line.line_kind}</Badge>
            )}
          </span>
        )}
        {line.product && resolution && resolution !== "manual" ? (
          <span className="ml-1 text-xs text-neutral-600 dark:text-neutral-400">{resolutionLabel[resolution] ?? resolution}</span>
        ) : null}
      </td>
      <td className="py-2 pr-3 text-right whitespace-nowrap tabular-nums">{line.qty !== null ? `${trimDecimal(line.qty)} × ${line.unit ?? ""}` : "—"}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{line.unit_price !== null ? formatMoney(line.unit_price, 2, 4) : "—"}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{line.line_total !== null ? formatMoney(line.line_total) : "—"}</td>
      <td className="py-2 pr-3">
        <span className="flex flex-wrap gap-1">
          {line.flags.map((f) => (
            <Badge key={f} tone="warn">
              {f.replaceAll("_", " ")}
            </Badge>
          ))}
        </span>
      </td>
      <td className="py-2">
        {voided ? (
          line.recorded ? (
            <Badge>voided</Badge>
          ) : null
        ) : isItem && resolution !== "ignored" ? (
          line.observation_id ? (
            <Badge tone="good">observed</Badge>
          ) : (
            <Badge tone="warn">no observation</Badge>
          )
        ) : null}
      </td>
    </tr>
  );
}

/** Reopen a committed manual purchase; the server voids and re-emits observations for changed lines. */
function EditPurchase({ purchase, onDone }: { purchase: Purchase; onDone: () => void }) {
  const update = useUpdatePurchase(purchase.id);
  const notice = useNotice();
  const [values, setValues] = useState(() => purchaseValues(purchase));
  // Saved lines taken out of the form that reached the price book: saving
  // voids their prices (#72, D13).
  const kept = new Set(values.lines.filter((l) => !isLineEmpty(l)).map((l) => l.id));
  const voids = purchase.lines.filter((l) => l.recorded && l.line_kind === "item" && !kept.has(l.id)).length;
  const prices = (n: number) => `${n} ${n === 1 ? "price" : "prices"}`;
  const save = (input: Parameters<typeof update.mutate>[0]) =>
    update.mutate(input, {
      onSuccess: () => {
        if (voids > 0) notice.show({ tone: "success", message: `Saved. ${prices(voids)} from removed lines ${voids === 1 ? "was" : "were"} voided.` });
        onDone();
      },
    });
  return (
    <PurchaseForm
      idPrefix="edit-purchase"
      heading="Edit purchase"
      values={values}
      onChange={setValues}
      existing={purchase}
      busy={update.isPending}
      error={update.error}
      submitLabel="Save changes"
      busyLabel="Saving…"
      onSubmit={save}
      warning={voids > 0 ? `Saving voids ${prices(voids)} from removed lines.` : null}
    >
      <Button variant="secondary" onClick={onDone} disabled={update.isPending}>
        Cancel
      </Button>
    </PurchaseForm>
  );
}
