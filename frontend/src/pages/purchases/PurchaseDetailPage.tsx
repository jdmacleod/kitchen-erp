import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { formatPack, productTitle, trimDecimal } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import {
  purchaseErrorMessage,
  purchaseLocationLabel,
  purchaseStatusLabel,
  resolutionLabel,
  sourceLabel,
  usePurchase,
  useReopenPurchase,
  useUpdatePurchase,
  type Purchase,
  type PurchaseLine,
  type Resolution,
} from "../../api/purchases";
import { Badge } from "../../components/catalog/fields";
import { PurchaseForm, purchaseValues } from "../../components/purchases/PurchaseForm";
import { ReviewPurchase } from "../../components/purchases/ReviewPurchase";
import { Alert, Button, Card, PageHeader, focusRing } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

/**
 * A purchase. Draft and reviewed purchases open in review mode (Phase 2D);
 * committed ones show their lines and observations, with Reopen to go back
 * to review and, for manual purchases, the entry form to edit them outright.
 */
export function PurchaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const purchase = usePurchase(id);
  const [editing, setEditing] = useState(false);
  const title = purchase.data
    ? `${purchase.data.vendor_location?.vendor.name ?? "Receipt"} · ${formatDateTime(purchase.data.purchased_at)}`
    : "Purchase";
  usePageTitle(title);

  // The first-run arc closes here, where the work finished, rather than on a later
  // visit to the home page that may be days away. The flag travels in router state
  // from the checklist through the entry form, which unmounts on success.
  //
  // Read once into state, then stripped from history, the way MapPage consumes
  // `?place`. Without the strip a reload or a back-navigation would congratulate
  // the same purchase again.
  const routerLocation = useLocation();
  const navigate = useNavigate();
  const [firstPurchase, setFirstPurchase] = useState(
    () => (routerLocation.state as { firstPurchase?: boolean } | null)?.firstPurchase === true,
  );
  useEffect(() => {
    if ((routerLocation.state as { firstPurchase?: boolean } | null)?.firstPurchase !== true) return;
    navigate(routerLocation.pathname, { replace: true, state: null });
  }, [routerLocation.state, routerLocation.pathname, navigate]);

  if (purchase.isPending) {
    return (
      <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
        Loading…
      </p>
    );
  }
  if (purchase.isError) {
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

  if (p.status !== "committed") {
    return (
      <>
        <PageHeader title={title}>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="warn">{purchaseStatusLabel[p.status]}</Badge>
            <span className="text-sm text-neutral-600 dark:text-neutral-400">{sourceLabel[p.source]}</span>
            <Link to="/purchases" className={`inline-flex min-h-10 items-center rounded-md px-2 text-sm underline ${focusRing}`}>
              All purchases
            </Link>
          </div>
        </PageHeader>
        <ReviewPurchase purchase={p} />
      </>
    );
  }

  // Only the committed branch carries the acknowledgement: manual entry commits at
  // once, so a purchase created from the checklist always lands here.
  return (
    <CommittedPurchase
      purchase={p}
      title={title}
      firstPurchase={firstPurchase}
      // The acknowledgement belongs to the first commit, not to every commit of
      // this purchase. Reopening retires it, so a reopen-and-recommit without
      // leaving the page does not congratulate the household twice.
      onReopened={() => setFirstPurchase(false)}
      onEdit={() => setEditing(true)}
    />
  );
}

function CommittedPurchase({
  purchase: p,
  title,
  firstPurchase,
  onReopened,
  onEdit,
}: {
  purchase: Purchase;
  title: string;
  firstPurchase: boolean;
  onReopened: () => void;
  onEdit: () => void;
}) {
  const reopen = useReopenPurchase(p.id);
  return (
    <>
      {firstPurchase ? (
        <Alert tone="success" className="mb-6">
          That is your kitchen set up. This purchase is in the price book, and the next one
          will have something to compare against.
        </Alert>
      ) : null}
      <PageHeader title={title}>
        <div className="flex flex-wrap gap-2">
          {p.source === "manual" ? (
            <Button variant="secondary" onClick={onEdit}>
              Edit
            </Button>
          ) : null}
          <Button variant="secondary" disabled={reopen.isPending} onClick={() => reopen.mutate(undefined, { onSuccess: onReopened })}>
            {reopen.isPending ? "Reopening…" : "Reopen"}
          </Button>
          <Link to="/purchases" className={`inline-flex min-h-10 items-center rounded-md px-2 text-sm underline ${focusRing}`}>
            All purchases
          </Link>
        </div>
      </PageHeader>

      <div className="flex flex-col gap-6">
        {reopen.error ? <Alert tone="error">{purchaseErrorMessage(reopen.error)}</Alert> : null}
        <Card>
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <Item label="Location">
              {p.vendor_location ? (
                <>
                  <Link to={`/vendors/${p.vendor_location.vendor.id}`} className={`rounded underline ${focusRing}`}>
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
              <Badge tone={p.status === "committed" ? "good" : "warn"}>{purchaseStatusLabel[p.status]}</Badge> <span>{sourceLabel[p.source]}</span>
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
                <tr className="border-b border-neutral-200 text-left text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:border-neutral-800">
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
                  <LineRow key={l.id} line={l} />
                ))}
              </tbody>
            </table>
          </div>
          {p.lines.some((l) => l.line_kind === "item" && l.resolution === "unmatched") ? (
            <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
              Unidentified lines wait in the{" "}
              <Link to="/to-identify" className={`rounded underline ${focusRing}`}>
                to-identify queue
              </Link>
              .
            </p>
          ) : null}
        </Card>
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

function LineRow({ line }: { line: PurchaseLine }) {
  const isItem = line.line_kind === "item";
  const resolution = line.resolution as Resolution | null;
  return (
    <tr className={isItem ? "" : "text-neutral-600 dark:text-neutral-400"}>
      <td className="py-2 pr-3 tabular-nums">{line.seq}</td>
      <td className="py-2 pr-3">
        {line.product ? (
          <>
            <Link to={`/products/${line.product.id}`} className={`rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
              {productTitle(line.product)}
            </Link>
            {formatPack(line.product.pack_qty, line.product.pack_unit) ? (
              <span className="text-neutral-600 dark:text-neutral-400"> · {formatPack(line.product.pack_qty, line.product.pack_unit)}</span>
            ) : null}
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
          <span className="ml-1 text-xs text-neutral-500">{resolutionLabel[resolution] ?? resolution}</span>
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
        {isItem && resolution !== "ignored" ? line.observation_id ? <Badge tone="good">observed</Badge> : <Badge tone="warn">no observation</Badge> : null}
      </td>
    </tr>
  );
}

/** Reopen a committed manual purchase; the server voids and re-emits observations for changed lines. */
function EditPurchase({ purchase, onDone }: { purchase: Purchase; onDone: () => void }) {
  const update = useUpdatePurchase(purchase.id);
  const [values, setValues] = useState(() => purchaseValues(purchase));
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
      onSubmit={(input) => update.mutate(input, { onSuccess: onDone })}
    >
      <Button variant="secondary" onClick={onDone} disabled={update.isPending}>
        Cancel
      </Button>
    </PurchaseForm>
  );
}
