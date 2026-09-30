import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import { purchaseErrorMessage, useApplyToIdentify, useToIdentify, type ToIdentifyGroup } from "../../api/purchases";
import { SelectField } from "../../components/catalog/fields";
import { NameProducts } from "../../components/purchases/NameProducts";
import { ProductPicker } from "../../components/purchases/ProductPicker";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";
import { useNotice } from "../../components/Notice";

/**
 * Unmatched lines across every committed purchase, grouped by vendor and
 * normalized text so one answer can settle every instance at once.
 */
export function ToIdentifyPage() {
  usePageTitle("To identify");
  const queue = useToIdentify();
  const groups = queue.data ?? [];
  // Naming in bulk (N1): offered once a few groups wait, in the URL so Back leaves it.
  const [params, setParams] = useSearchParams();
  const naming = params.get("mode") === "name";
  const offerNaming = !naming && groups.length >= NAMING_MIN;
  // Once a group is answered it leaves the list, and the next one is where the
  // work continues: its product box takes focus (#88).
  const focusKey = useRef<string | null>(null);
  useEffect(() => {
    const key = focusKey.current;
    if (key === null) return;
    focusKey.current = null;
    const at = groups.findIndex((g) => groupKey(g) === key);
    // Its product box, or the ingredient box of the new product it is being given.
    if (at >= 0) (document.getElementById(`identify-${at}`) ?? document.getElementById(`identify-${at}-new-ingredient`))?.focus();
  });

  return (
    <>
      <PageHeader title="To identify" description="Receipt lines that don't have a product yet. Match one and the vendor's wording is learned.">
        {offerNaming ? <Button onClick={() => setParams({ mode: "name" })}>Name the new products</Button> : null}
        {naming ? (
          <Button variant="secondary" onClick={() => setParams({})}>
            One at a time
          </Button>
        ) : null}
      </PageHeader>
      {naming ? (
        <NameProducts />
      ) : queue.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : queue.isError ? (
        <Alert tone="error">{errorMessage(queue.error)}</Alert>
      ) : groups.length === 0 ? (
        <EmptyState
          title="All lines identified"
          action={
            <Link to="/" className={secondaryLinkClass}>
              Back to Home
            </Link>
          }
        >
          Every committed receipt line has a product or is ignored.
        </EmptyState>
      ) : (
        <ul aria-label="Lines to identify" className="flex flex-col gap-4">
          {groups.map((g, i) => (
            <li key={groupKey(g)}>
              <Card>
                <GroupCard
                  group={g}
                  index={i}
                  onAnswered={() => {
                    focusKey.current = groupKey(groups[i + 1] ?? groups[i - 1]);
                  }}
                />
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** How many groups must wait before naming them in bulk is offered (N1). */
const NAMING_MIN = 3;

function groupKey(g: ToIdentifyGroup | undefined): string | null {
  return g ? `${g.vendor.id}:${g.raw_text_norm}` : null;
}

function GroupCard({ group, index, onAnswered }: { group: ToIdentifyGroup; index: number; onAnswered: () => void }) {
  const apply = useApplyToIdentify();
  const notice = useNotice();
  // The group leaves the list once applied, so the confirmation lives in the Notice.
  const onApplied = ({ applied }: { applied: number }) => {
    notice.show({ tone: "success", message: `Applied to ${applied} ${applied === 1 ? "line" : "lines"}.` });
    onAnswered();
  };
  const [all, setAll] = useState(true);
  const [lineId, setLineId] = useState(group.lines[0]?.line_id ?? "");
  const id = `identify-${index}`;
  const scope = () => (all ? {} : { line_ids: [lineId] });

  return (
    <div className="flex flex-col gap-3" role="group" aria-label={`${group.vendor.name}: ${group.raw_text_norm ?? "(no text)"}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-medium">
          <span className="font-mono">{group.raw_text_norm ?? "(no text)"}</span>
          <span className="text-neutral-600 dark:text-neutral-400"> at {group.vendor.name}</span>
        </h2>
        <span className="text-xs text-neutral-600 dark:text-neutral-400">
          {group.line_count} {group.line_count === 1 ? "line" : "lines"}
        </span>
      </div>
      {apply.error ? <Alert tone="error">{purchaseErrorMessage(apply.error)}</Alert> : null}
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-700 dark:text-neutral-300">
        {group.lines.map((l) => (
          <li key={l.line_id}>
            <Link to={`/shop/purchases/${l.purchase_id}`} className={`rounded underline ${focusRing}`}>
              {formatDate(l.purchased_at)}
            </Link>
            {l.raw_text && l.raw_text !== group.raw_text_norm ? <span className="font-mono"> {l.raw_text}</span> : null}
            {l.line_total !== null ? <span> · {formatMoney(l.line_total)}</span> : null}
          </li>
        ))}
      </ul>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
        <ProductPicker
          id={id}
          label="Product"
          value={null}
          lineText={group.raw_text_norm}
          onChange={(p) => p && apply.mutate({ vendor_id: group.vendor.id, raw_text_norm: group.raw_text_norm ?? "", product_id: p.id, ...scope() }, { onSuccess: onApplied })}
          disabled={apply.isPending}
        />
        <div className="flex items-end">
          <Button variant="secondary" disabled={apply.isPending} onClick={() => apply.mutate({ vendor_id: group.vendor.id, raw_text_norm: group.raw_text_norm ?? "", ignore: true, ...scope() }, { onSuccess: onApplied })}>
            Ignore
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="inline-flex min-h-11 lg:min-h-10 items-center gap-2 text-sm">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} className={`size-4 ${focusRing}`} />
          Apply to all {group.line_count}
        </label>
        {!all && group.lines.length > 1 ? (
          <SelectField id={`${id}-line`} label="Only this line" value={lineId} onChange={(e) => setLineId(e.target.value)} className="w-56">
            {group.lines.map((l) => (
              <option key={l.line_id} value={l.line_id}>
                {formatDate(l.purchased_at)} {l.line_total !== null ? formatMoney(l.line_total) : ""}
              </option>
            ))}
          </SelectField>
        ) : null}
      </div>
    </div>
  );
}
