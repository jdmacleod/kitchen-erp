import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { sourceLabel, useObservations, useVoidObservation, type Observation } from "../../api/purchases";
import { formatMoney, stripZeros } from "../../lib/decimal";
import { formatDateTime } from "../../lib/format";
import { Alert, Button, Card, Field, focusRing } from "../ui";
import { PromoBadge } from "./PriceAge";

const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * Every price recorded for a product, with the way to correct each (#73).
 *
 * A shelf price is voided here, with a reason: voiding is how a fact is taken
 * back, and the original stays for audit. A price that came from a purchase is
 * corrected in that purchase instead; voiding it here would leave the purchase
 * saying the line was observed.
 */
export function PriceRecords({ productId }: { productId: string }) {
  const [showVoided, setShowVoided] = useState(false);
  const records = useObservations({ product_id: productId, include_voided: showVoided });
  const items = records.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Price records</h2>
        <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-9">
          <input type="checkbox" checked={showVoided} onChange={(e) => setShowVoided(e.target.checked)} className={`size-4 ${focusRing}`} />
          Show voided
        </label>
      </div>
      {records.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : records.isError ? (
        <Alert tone="error">{errorMessage(records.error)}</Alert>
      ) : items.length === 0 ? (
        <p className={`text-sm ${muted}`}>{showVoided ? "No prices recorded yet." : "No current prices. Tick Show voided to see voided ones."}</p>
      ) : (
        <>
          <ul aria-label="Price records" className="flex flex-col divide-y divide-neutral-200 dark:divide-neutral-800">
            {items.map((o) => (
              <PriceRecord key={o.id} record={o} />
            ))}
          </ul>
          {records.hasNextPage ? (
            <div className="mt-3">
              <Button variant="secondary" disabled={records.isFetchingNextPage} onClick={() => records.fetchNextPage()}>
                {records.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}

function PriceRecord({ record }: { record: Observation }) {
  const [voiding, setVoiding] = useState(false);
  const [reason, setReason] = useState("");
  const voidPrice = useVoidObservation();
  const where = record.vendor_location.name === record.vendor_location.vendor.name ? record.vendor_location.name : `${record.vendor_location.vendor.name} — ${record.vendor_location.name}`;
  const fieldId = `void-reason-${record.id}`;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const why = reason.trim();
    if (!why) return;
    voidPrice.mutate({ id: record.id, reason: why }, { onSuccess: () => setVoiding(false) });
  };

  return (
    <li className="flex flex-col gap-2 py-3" data-testid="price-record">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className={`flex min-w-0 flex-col ${record.voided ? muted : ""}`}>
          <span className={`tabular-nums ${record.voided ? "line-through" : ""}`}>
            {formatMoney(record.price)} / {stripZeros(record.qty)} {record.unit} <PromoBadge promo={record.is_promo} />
          </span>
          <span className={`text-xs ${muted}`}>
            {where} · <time dateTime={record.observed_at}>{formatDateTime(record.observed_at)}</time> · {record.source === "shelf" ? "Shelf price" : `${sourceLabel[record.source]} purchase`}
          </span>
          {record.voided ? <span className={`text-xs ${muted}`}>Voided{record.void_reason ? `: ${record.void_reason}` : ""}</span> : null}
        </div>
        {record.voided ? null : record.purchase_id ? (
          <Link to={`/shop/purchases/${encodeURIComponent(record.purchase_id)}`} className={`inline-flex min-h-11 items-center text-sm underline-offset-2 hover:underline lg:min-h-0 ${focusRing}`}>
            Correct in its purchase
          </Link>
        ) : voiding ? null : (
          <Button variant="danger" onClick={() => setVoiding(true)} aria-label={`Void the ${formatMoney(record.price)} price at ${where}`}>
            Void
          </Button>
        )}
      </div>
      {voiding ? (
        <form onSubmit={onSubmit} className="flex flex-col gap-2" aria-label={`Void the ${formatMoney(record.price)} price at ${where}`}>
          {voidPrice.isError ? <Alert tone="error">{errorMessage(voidPrice.error)}</Alert> : null}
          <Field
            id={fieldId}
            label="Why is it wrong?"
            autoComplete="off"
            required
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            hint="Kept with the voided price. It leaves the charts, cheapest and compare."
            autoFocus
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="danger" disabled={voidPrice.isPending || reason.trim() === ""}>
              {voidPrice.isPending ? "Voiding…" : "Void price"}
            </Button>
            <Button variant="secondary" onClick={() => setVoiding(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </li>
  );
}
