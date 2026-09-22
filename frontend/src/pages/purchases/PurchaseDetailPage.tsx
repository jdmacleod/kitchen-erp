import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { formatPack, productTitle, trimDecimal } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { purchaseStatusLabel, sourceLabel, usePurchase, useUpdatePurchase, type Purchase, type PurchaseLine } from "../../api/purchases";
import { Badge } from "../../components/catalog/fields";
import { PurchaseForm, purchaseValues } from "../../components/purchases/PurchaseForm";
import { Alert, Button, Card, PageHeader, focusRing } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

export function PurchaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const purchase = usePurchase(id);
  const [editing, setEditing] = useState(false);
  const title = purchase.data ? `${purchase.data.vendor_location.vendor.name} · ${formatDateTime(purchase.data.purchased_at)}` : "Purchase";
  usePageTitle(title);

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

  return (
    <>
      <PageHeader title={title}>
        <div className="flex flex-wrap gap-2">
          {p.source === "manual" ? (
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Edit
            </Button>
          ) : null}
          <Link to="/purchases" className={`inline-flex min-h-10 items-center rounded-md px-2 text-sm underline ${focusRing}`}>
            All purchases
          </Link>
        </div>
      </PageHeader>

      <div className="flex flex-col gap-6">
        <Card>
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <Item label="Location">
              <Link to={`/vendors/${p.vendor_location.vendor.id}`} className={`rounded underline ${focusRing}`}>
                {p.vendor_location.vendor.name}
              </Link>
              {p.vendor_location.name !== p.vendor_location.vendor.name ? ` — ${p.vendor_location.name}` : ""}
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
                      {f}
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
            {line.raw_text ?? "—"} {isItem ? <Badge tone="warn">unresolved</Badge> : <Badge>{line.line_kind}</Badge>}
          </span>
        )}
      </td>
      <td className="py-2 pr-3 text-right whitespace-nowrap tabular-nums">{line.qty !== null ? `${trimDecimal(line.qty)} × ${line.unit ?? ""}` : "—"}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{line.unit_price !== null ? formatMoney(line.unit_price, 2, 4) : "—"}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{line.line_total !== null ? formatMoney(line.line_total) : "—"}</td>
      <td className="py-2 pr-3">
        <span className="flex flex-wrap gap-1">
          {line.flags.map((f) => (
            <Badge key={f} tone="warn">
              {f}
            </Badge>
          ))}
        </span>
      </td>
      <td className="py-2">{isItem ? line.observation_id ? <Badge tone="good">observed</Badge> : <Badge tone="warn">no observation</Badge> : null}</td>
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
