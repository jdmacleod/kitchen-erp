import { useState } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { jobInFlight, useIngestJobs, type IngestJob } from "../../api/ingest";
import { itemLines, purchaseStatusLabel, purchaseStatusTone, sourceLabel, usePurchases, type Purchase, type PurchaseStatus } from "../../api/purchases";
import { Badge } from "../../components/catalog/fields";
import { SegmentedControl } from "../../components/SegmentedControl";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing, primaryLinkClass, secondaryLinkClass, tapTarget } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { LG_QUERY, useMediaQuery } from "../../lib/useMediaQuery";
import { usePageTitle } from "../../lib/usePageTitle";

type Filter = PurchaseStatus | "all";

/** Where a purchase came from, in the words spec 10 uses. */
function fromLabel(p: Purchase): string {
  if (p.source === "receipt") return "Receipt";
  if (p.source === "manual") return "By hand";
  return sourceLabel[p.source];
}

/** "3", or "50+" when there is a further page. */
function countLabel(n: number, more: boolean): string {
  return more ? `${n}+` : String(n);
}

/**
 * Every purchase, newest first (docs/spec/10, Shop: purchases). Receipts still
 * being read are rows too, with a Reading pill, so an upload never looks lost (G1).
 */
export function PurchasesPage() {
  usePageTitle("Purchases");
  const [filter, setFilter] = useState<Filter>("all");
  const status = filter === "all" ? "" : filter;
  const purchases = usePurchases({ status });
  const items = purchases.data?.pages.flatMap((p) => p.items) ?? [];
  // The Drafts count, from the first page of drafts.
  const drafts = usePurchases({ status: "draft" });
  const draftPage = drafts.data?.pages[0];
  const draftCount = draftPage ? countLabel(draftPage.items.length, Boolean(draftPage.next_cursor)) : null;
  // Receipts in flight belong with the drafts they are about to become.
  const jobs = useIngestJobs();
  const reading = filter === "all" || filter === "draft" ? (jobs.data ?? []).filter(jobInFlight) : [];
  // The table at lg and wider, a list below it (G19).
  const wide = useMediaQuery(LG_QUERY);

  return (
    <>
      <PageHeader title="Purchases" description="Everything you've bought, newest first. Drafts wait for you to finish them.">
        <div className="flex flex-wrap gap-2">
          <Link to="/shop/receipts" className={secondaryLinkClass}>
            Scan a receipt
          </Link>
          <Link to="/shop/purchases/new" className={primaryLinkClass}>
            New purchase
          </Link>
        </div>
      </PageHeader>

      {/* The phone's drafts banner (10, Phone: purchases): one tap to the drafts. */}
      {filter === "all" && draftPage && draftPage.items.length > 0 ? (
        <button
          type="button"
          onClick={() => setFilter("draft")}
          className={`mb-4 flex min-h-11 w-full items-center justify-between gap-3 rounded-lg border border-amber-400 px-4 text-left text-sm text-amber-900 lg:hidden dark:border-amber-600 dark:text-amber-200 ${focusRing}`}
        >
          <span className="font-medium">
            {draftCount} {draftPage.items.length === 1 && !draftPage.next_cursor ? "draft" : "drafts"} to finish
          </span>
          <span aria-hidden="true">›</span>
        </button>
      ) : null}

      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-medium">All purchases</h2>
          <SegmentedControl
            label="Status"
            options={[
              { value: "all", label: "All" },
              { value: "draft", label: draftCount ? `Drafts ${draftCount}` : "Drafts" },
              { value: "reviewed", label: purchaseStatusLabel.reviewed },
              { value: "committed", label: purchaseStatusLabel.committed },
            ]}
            value={filter}
            onChange={setFilter}
          />
        </div>

        {purchases.isPending ? (
          <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
            Loading…
          </p>
        ) : purchases.isError ? (
          <Alert tone="error">{errorMessage(purchases.error)}</Alert>
        ) : items.length === 0 && reading.length === 0 && status ? (
          // Filtered-empty and truly empty say different things (G11).
          <EmptyState
            title={`No ${purchaseStatusLabel[status].toLowerCase()} purchases`}
            action={
              <Button variant="secondary" onClick={() => setFilter("all")}>
                Show all
              </Button>
            }
          >
            Nothing has this status right now.
          </EmptyState>
        ) : items.length === 0 && reading.length === 0 ? (
          <EmptyState
            title="No purchases yet"
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Link to="/shop/receipts" className={secondaryLinkClass}>
                  Scan a receipt
                </Link>
                <Link to="/shop/purchases/new" className={primaryLinkClass}>
                  New purchase
                </Link>
              </div>
            }
          >
            Scan a receipt or enter one by hand, and its prices start your price book.
          </EmptyState>
        ) : (
          <>
            {wide ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Purchases">
                <thead>
                  <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                    <th className="py-2 pr-3">Date</th>
                    <th className="py-2 pr-3">Where</th>
                    <th className="py-2 pr-3">From</th>
                    <th className="py-2 pr-3 text-right">Lines</th>
                    <th className="py-2 pr-3 text-right">Total</th>
                    <th className="py-2">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                  {reading.map((job) => (
                    <ReadingRow key={job.id} job={job} />
                  ))}
                  {items.map((p) => (
                    <tr key={p.id}>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <Link to={`/shop/purchases/${p.id}`} className={`${tapTarget} rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
                          {formatDate(p.purchased_at)}
                        </Link>
                      </td>
                      <td className="py-2 pr-3">
                        {p.vendor_location ? (
                          <>
                            {p.vendor_location.vendor.name}
                            {p.vendor_location.name !== p.vendor_location.vendor.name ? (
                              <span className="text-neutral-600 dark:text-neutral-400"> — {p.vendor_location.name}</span>
                            ) : null}
                          </>
                        ) : p.status === "committed" ? (
                          <span className="text-neutral-600 italic dark:text-neutral-400">No location</span>
                        ) : (
                          // A draft cannot commit without one; say so where it is missing.
                          <span className="font-medium text-amber-800 dark:text-amber-300">Location needed</span>
                        )}
                      </td>
                      <td className="py-2 pr-3">{fromLabel(p)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{itemLines(p).length}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{formatMoney(p.total ?? p.computed_total)}</td>
                      <td className="py-2">
                        <Badge tone={purchaseStatusTone[p.status]}>{purchaseStatusLabel[p.status]}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            ) : (
              // Phone: purchases (10). One row per purchase: where, then the date
              // and line count, with the total and status at the right.
              <ul aria-label="Purchases" className="-mx-1 divide-y divide-neutral-200 dark:divide-neutral-800">
                {reading.map((job) => (
                  <li key={job.id} data-testid="reading-row">
                    <Link to={`/shop/receipts?job=${encodeURIComponent(job.id)}`} className={`flex min-h-14 items-center justify-between gap-3 rounded-md px-1 py-2 text-neutral-900 dark:text-neutral-100 ${focusRing}`}>
                      <span className="min-w-0">
                        <span className="block font-medium">Receipt being read</span>
                        <span className="block text-xs text-neutral-600 dark:text-neutral-400">{job.created_at ? formatDate(job.created_at) : "Just now"}</span>
                      </span>
                      <Badge>Reading</Badge>
                    </Link>
                  </li>
                ))}
                {items.map((p) => (
                  <li key={p.id}>
                    <Link to={`/shop/purchases/${p.id}`} className={`flex min-h-14 items-center justify-between gap-3 rounded-md px-1 py-2 text-neutral-900 dark:text-neutral-100 ${focusRing}`}>
                      <span className="min-w-0">
                        <span className="block truncate font-medium">
                          {p.vendor_location ? (
                            p.vendor_location.vendor.name
                          ) : p.status === "committed" ? (
                            "No location"
                          ) : (
                            <span className="text-amber-800 dark:text-amber-300">Location needed</span>
                          )}
                        </span>
                        <span className="block text-xs text-neutral-600 dark:text-neutral-400">
                          {formatDate(p.purchased_at)} · {itemLines(p).length} {itemLines(p).length === 1 ? "line" : "lines"}
                        </span>
                      </span>
                      <span className="flex shrink-0 flex-col items-end gap-1">
                        <span className="text-sm font-semibold tabular-nums">{formatMoney(p.total ?? p.computed_total)}</span>
                        <Badge tone={purchaseStatusTone[p.status]}>{purchaseStatusLabel[p.status]}</Badge>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {purchases.hasNextPage ? (
              <div className="mt-3">
                <Button variant="secondary" disabled={purchases.isFetchingNextPage} onClick={() => purchases.fetchNextPage()}>
                  {purchases.isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Card>
    </>
  );
}

/** A receipt still being read: no purchase yet, so it links to its job (G1). */
function ReadingRow({ job }: { job: IngestJob }) {
  return (
    <tr data-testid="reading-row">
      <td className="py-2 pr-3 whitespace-nowrap">
        <Link to={`/shop/receipts?job=${encodeURIComponent(job.id)}`} className={`${tapTarget} rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
          {job.created_at ? formatDate(job.created_at) : "Just now"}
        </Link>
      </td>
      <td className="py-2 pr-3 text-neutral-600 dark:text-neutral-400">Being read</td>
      <td className="py-2 pr-3">Receipt</td>
      <td className="py-2 pr-3 text-right text-neutral-600 dark:text-neutral-400">—</td>
      <td className="py-2 pr-3 text-right text-neutral-600 dark:text-neutral-400">—</td>
      <td className="py-2">
        <Badge>Reading</Badge>
      </td>
    </tr>
  );
}
