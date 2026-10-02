import { useState } from "react";
import { Link } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import { useLocations } from "../../api/geo";
import { useDecidePriceChange, usePriceChanges, type PriceChange } from "../../api/proposals";
import { Badge } from "../../components/catalog/fields";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { formatMoney, stripZeros } from "../../lib/decimal";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * Posted prices the lookup helper saw change (04, 2N; PR2): one inbox row leads
 * here, and nothing is recorded until a person accepts a row.
 */
export function PostedPricesPage() {
  usePageTitle("Posted prices");
  const changes = usePriceChanges();
  const items = changes.data?.items ?? [];
  return (
    <>
      <PageHeader title="Posted prices" description="Prices the lookup helper saw change on store pages. Accept the ones to record; posted prices are not counted in cheapest." />
      {changes.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : changes.isError ? (
        <Alert tone="error">{errorMessage(changes.error)}</Alert>
      ) : items.length === 0 ? (
        <EmptyState
          title="No posted prices waiting"
          action={
            <Link to="/" className={secondaryLinkClass}>
              Back to Home
            </Link>
          }
        >
          Changed prices from the lookup helper appear here until you decide on them.
        </EmptyState>
      ) : (
        <ul aria-label="Posted prices" className="flex flex-col gap-3">
          {items.map((c) => (
            <li key={c.id}>
              <Card>
                <ChangeRow change={c} />
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function ChangeRow({ change }: { change: PriceChange }) {
  const decide = useDecidePriceChange();
  const [store, setStore] = useState("");
  const needsStore = isApiError(decide.error) && decide.error.code === "location_required";
  const locations = useLocations({ vendor_id: change.vendor_id }, needsStore);
  return (
    <div className="flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Link to={`/catalog/products/${change.product_id}`} className={`rounded font-medium underline ${focusRing}`}>
          {change.product_name}
        </Link>
        <span className="tabular-nums">
          {formatMoney(change.amount)} / {stripZeros(change.qty)} {change.unit} {change.is_promo ? <Badge tone="good">on sale</Badge> : null}
        </span>
      </div>
      <p className={muted}>
        {change.vendor_name} · seen <time dateTime={change.seen_at}>{formatDateTime(change.seen_at)}</time>
      </p>
      <p className={`break-all text-xs ${muted}`}>{change.canonical_url}</p>
      {needsStore ? (
        <label className="flex flex-col gap-1">
          At which {change.vendor_name} store?
          <select
            value={store}
            onChange={(e) => setStore(e.target.value)}
            className={`min-h-11 rounded-md border border-neutral-300 bg-white px-2 lg:min-h-9 dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
          >
            <option value="">Choose a store</option>
            {(locations.data ?? []).map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
      ) : decide.isError ? (
        <Alert tone="error">{errorMessage(decide.error)}</Alert>
      ) : null}
      <span className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          disabled={decide.isPending || (needsStore && !store)}
          onClick={() => decide.mutate({ id: change.id, accept: true, vendor_location_id: store || undefined })}
        >
          Accept
        </Button>
        <Button variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate({ id: change.id, accept: false })}>
          Reject
        </Button>
      </span>
    </div>
  );
}
