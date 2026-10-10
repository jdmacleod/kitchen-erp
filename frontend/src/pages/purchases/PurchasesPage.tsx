import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import { jobInFlight, useIngestJobs, type IngestJob } from "../../api/ingest";
import { itemLines, purchaseKeys, purchaseStatusLabel, purchaseStatusTone, sourceLabel, usePurchases, type Purchase, type PurchaseSort, type PurchaseStatus, type SortDir } from "../../api/purchases";
import { Badge } from "../../components/catalog/fields";
import { SegmentedControl } from "../../components/SegmentedControl";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing, primaryLinkClass, secondaryLinkClass, tapTarget } from "../../components/ui";
import { TrustBadge } from "../../components/purchases/TrustBadge";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { LG_QUERY, useMediaQuery } from "../../lib/useMediaQuery";
import { usePageTitle } from "../../lib/usePageTitle";

type Filter = PurchaseStatus | "all";

/** Where a purchase came from, in the words spec 10 uses. */
function fromLabel(p: Purchase): string {
  if (p.source === "receipt") return "Receipt";
  if (p.source === "manual") return "By hand";
  return sourceLabel[p.source];
}

const SORTS: PurchaseSort[] = ["date", "where", "total"];
/** The first click on a heading: newest, A to Z, largest (spec 10). */
const FIRST_DIR: Record<PurchaseSort, SortDir> = { date: "desc", where: "asc", total: "desc" };
/** The phone's "Sort by" choices, each a sort and a direction. */
const SORT_CHOICES: { value: string; label: string }[] = [
  { value: "date:desc", label: "Newest" },
  { value: "date:asc", label: "Oldest" },
  { value: "where:asc", label: "Store A–Z" },
  { value: "total:desc", label: "Largest total" },
  { value: "total:asc", label: "Smallest total" },
];

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
  // Removed purchases are a separate view, kept in the URL (#74, D6), so the
  // segments stay the four statuses a person works with.
  const [params, setParams] = useSearchParams();
  const voidedView = params.get("status") === "voided";
  const setParam = (changes: Record<string, string | null>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(changes)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        return next;
      },
      { replace: true },
    );
  const showAll = () => {
    setFilter("all");
    setParam({ status: null });
  };
  // Find and sort (spec 10, issue 292), kept in the URL; the default order is left out of it.
  const q = params.get("q")?.trim() ?? "";
  const rawSort = params.get("sort");
  const sort: PurchaseSort = SORTS.includes(rawSort as PurchaseSort) ? (rawSort as PurchaseSort) : "date";
  const rawDir = params.get("dir");
  const dir: SortDir = rawDir === "asc" || rawDir === "desc" ? rawDir : FIRST_DIR[sort];
  const defaultOrder = sort === "date" && dir === "desc";
  const setOrder = (next: PurchaseSort, nextDir: SortDir) =>
    setParam(next === "date" && nextDir === "desc" ? { sort: null, dir: null } : { sort: next, dir: nextDir });
  const onHeading = (column: PurchaseSort) =>
    setOrder(column, column === sort ? (dir === "asc" ? "desc" : "asc") : FIRST_DIR[column]);
  const [text, setText] = useState(q);
  // The field follows ?q= when it changes from outside (Back, a link); the URL
  // takes only text the debounce has settled on (the Products pattern).
  const [seenQ, setSeenQ] = useState(q);
  if (q !== seenQ) {
    setSeenQ(q);
    setText(q);
  }
  const debounced = useDebouncedValue(text, 300);
  useEffect(() => {
    if (debounced === text && debounced.trim() !== q) setParam({ q: debounced.trim() || null });
    // Only the typed text drives the URL; q changing on its own (Back) is read above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  const clearSearch = () => {
    setText("");
    setParam({ q: null });
  };
  const status = voidedView ? "voided" : filter === "all" ? "" : filter;
  const purchases = usePurchases({ status, q: q || undefined, sort: defaultOrder ? undefined : sort, dir: defaultOrder ? undefined : dir });
  const items = purchases.data?.pages.flatMap((p) => p.items) ?? [];
  // The Drafts count, from the first page of drafts.
  const drafts = usePurchases({ status: "draft" });
  const draftPage = drafts.data?.pages[0];
  const draftCount = draftPage ? countLabel(draftPage.items.length, Boolean(draftPage.next_cursor)) : null;
  // The same for removed purchases, for the "Show voided" link.
  const voided = usePurchases({ status: "voided" });
  const voidedPage = voided.data?.pages[0];
  const voidedCount = voidedPage && voidedPage.items.length > 0 ? countLabel(voidedPage.items.length, Boolean(voidedPage.next_cursor)) : null;
  // Receipts in flight belong with the drafts they are about to become. Asked
  // for by status, so an old upload still being read is never paged out by
  // newer finished ones.
  const pendingJobs = useIngestJobs("pending");
  const runningJobs = useIngestJobs("running");
  const inFlight = [...(pendingJobs.data ?? []), ...(runningJobs.data ?? [])].filter(jobInFlight);
  // A job that has already made its draft is listed as that draft.
  const readingJobs = inFlight.filter((j) => !j.purchase_id).sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  // Being read has no date or total yet: those rows lead only the default, unsearched order.
  const showsReading = !voidedView && (filter === "all" || filter === "draft") && defaultOrder && !q;
  const reading = showsReading ? readingJobs : [];
  // Could not check: never claim there is nothing being read (CLAUDE.md: an
  // error is never an empty state).
  const jobsFailed = showsReading && (pendingJobs.isError || runningJobs.isError);

  // A Reading row that goes away has become a draft: fetch the purchases again
  // so it appears, rather than vanishing until something else refetches.
  const client = useQueryClient();
  const readingIds = readingJobs.map((j) => j.id).join(",");
  const lastReading = useRef(readingIds);
  useEffect(() => {
    const before = lastReading.current.split(",").filter(Boolean);
    lastReading.current = readingIds;
    const now = new Set(readingIds.split(","));
    if (before.some((id) => !now.has(id))) void client.invalidateQueries({ queryKey: purchaseKeys.purchases });
  }, [readingIds, client]);
  // The table at lg and wider, a list below it (G19).
  const wide = useMediaQuery(LG_QUERY);

  return (
    <>
      <PageHeader
        title="Purchases"
        description={
          q && purchases.data
            ? `${countLabel(items.length, Boolean(purchases.hasNextPage))} ${items.length === 1 && !purchases.hasNextPage ? "purchase matches" : "purchases match"} “${q}”.`
            : // Mention removed purchases only when there is a link to them (issue 247).
              `Everything you've bought, newest first. Drafts wait for you to finish them.${voidedCount ? " Removed purchases are under Show voided." : ""}`
        }
      >
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
      {filter === "all" && !voidedView && draftPage && draftPage.items.length > 0 ? (
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
          <h2 className="text-lg font-medium">{voidedView ? "Voided purchases" : "All purchases"}</h2>
          {voidedView ? (
            <Button variant="secondary" onClick={showAll}>
              Back to all
            </Button>
          ) : (
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
          )}
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <label htmlFor="purchase-find" className="sr-only">
            Find a store or item
          </label>
          <input
            id="purchase-find"
            type="text"
            enterKeyHint="search"
            autoComplete="off"
            maxLength={200}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Find a store or item"
            className={`min-h-12 min-w-0 flex-1 basis-64 rounded-lg border border-neutral-300 bg-white px-4 text-base dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
          />
          {!wide ? (
            <label className="flex items-center gap-2 text-sm">
              <span className="text-neutral-600 dark:text-neutral-400">Sort by</span>
              <select
                id="purchase-sort"
                value={`${sort}:${dir}`}
                onChange={(e) => {
                  const [nextSort, nextDir] = e.target.value.split(":") as [PurchaseSort, SortDir];
                  setOrder(nextSort, nextDir);
                }}
                className={`min-h-11 rounded-md border border-neutral-300 bg-white px-2 text-sm dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
              >
                {SORT_CHOICES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>

        {purchases.isPending ? (
          <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
            Loading…
          </p>
        ) : purchases.isError ? (
          <Alert tone="error">{errorMessage(purchases.error)}</Alert>
        ) : items.length === 0 && reading.length === 0 && jobsFailed ? (
          <JobsFailed onRetry={() => void Promise.all([pendingJobs.refetch(), runningJobs.refetch()])} />
        ) : items.length === 0 && reading.length === 0 && q ? (
          <EmptyState
            title={`No purchases match “${q}”`}
            action={
              <Button variant="secondary" onClick={clearSearch}>
                Clear search
              </Button>
            }
          >
            Try a store, a location or another word from a receipt.
          </EmptyState>
        ) : items.length === 0 && reading.length === 0 && status ? (
          // Filtered-empty and truly empty say different things (G11).
          <EmptyState
            title={`No ${purchaseStatusLabel[status].toLowerCase()} purchases`}
            action={
              <Button variant="secondary" onClick={showAll}>
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
            {jobsFailed ? (
              <div className="mb-3">
                <JobsFailed onRetry={() => void Promise.all([pendingJobs.refetch(), runningJobs.refetch()])} />
              </div>
            ) : null}
            {wide ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Purchases">
                <thead>
                  <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                    <SortHeading column="date" label="Date" sort={sort} dir={dir} onSort={onHeading} />
                    <SortHeading column="where" label="Where" sort={sort} dir={dir} onSort={onHeading} />
                    <th className="py-2 pr-3">From</th>
                    <th className="py-2 pr-3 text-right">Lines</th>
                    <SortHeading column="total" label="Total" sort={sort} dir={dir} onSort={onHeading} align="right" />
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
                      <td className="py-2 pr-3 text-right tabular-nums">
                        <Total purchase={p} />
                      </td>
                      <td className="py-2">
                        <StatusBadge purchase={p} />
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
                        <span className="block text-xs text-neutral-600 dark:text-neutral-400">{uploaded(job)}</span>
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
                        <span className="text-sm font-semibold tabular-nums">
                          <Total purchase={p} />
                        </span>
                        <StatusBadge purchase={p} />
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
        {!voidedView && (voidedCount || voided.isError) ? (
          <p className="mt-3 text-sm">
            <Link to={`?${new URLSearchParams([...params.entries(), ["status", "voided"]]).toString()}`} className={`${tapTarget} rounded underline ${focusRing}`}>
              {/* Without a count when it couldn't be checked: the way in stays. */}
              {voidedCount ? `Show voided (${voidedCount})` : "Show voided"}
            </Link>
          </p>
        ) : null}
      </Card>
    </>
  );
}

/** A heading that sorts the list; the sorted one says which way (aria-sort). */
function SortHeading({
  column,
  label,
  sort,
  dir,
  onSort,
  align,
}: {
  column: PurchaseSort;
  label: string;
  sort: PurchaseSort;
  dir: SortDir;
  onSort: (column: PurchaseSort) => void;
  align?: "right";
}) {
  const active = column === sort;
  return (
    <th className={`py-1 pr-3 ${align === "right" ? "text-right" : ""}`} aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex min-h-9 items-center gap-1 rounded font-semibold hover:text-neutral-900 dark:hover:text-neutral-100 ${active ? "text-neutral-900 dark:text-neutral-100" : ""} ${focusRing}`}
      >
        {label}
        <span aria-hidden="true" className="w-3 text-center">
          {active ? (dir === "asc" ? "↑" : "↓") : ""}
        </span>
      </button>
    </th>
  );
}

/** The total; a voided purchase's is struck through, since it no longer counts (D18). */
function Total({ purchase: p }: { purchase: Purchase }) {
  const amount = formatMoney(p.total ?? p.computed_total);
  if (p.status !== "voided") return <>{amount}</>;
  return <s className="font-normal text-neutral-600 dark:text-neutral-400">{amount}</s>;
}

function JobsFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <Alert tone="error">
      <span className="flex flex-wrap items-center justify-between gap-2">
        <span>Couldn&apos;t check for receipts being read.</span>
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      </span>
    </Alert>
  );
}

/** When it was uploaded: a receipt being read has no purchase date yet. */
function uploaded(job: IngestJob): string {
  const when = job.uploaded_at ?? job.created_at;
  return when ? `Uploaded ${formatDate(when)}` : "Uploaded just now";
}

/**
 * A receipt still being read: no purchase yet, so it links to its job (G1).
 * These lead the list rather than sort among dated purchases: the date a
 * purchase has is when it was bought, and a receipt being read has none yet.
 */
function ReadingRow({ job }: { job: IngestJob }) {
  return (
    <tr data-testid="reading-row">
      <td className="py-2 pr-3 whitespace-nowrap">
        <Link to={`/shop/receipts?job=${encodeURIComponent(job.id)}`} className={`${tapTarget} rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
          {uploaded(job)}
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

/**
 * A purchase's status; a draft far off its printed total says so before it is
 * opened (issue 121, ruling R2).
 */
function StatusBadge({ purchase }: { purchase: Purchase }) {
  if (purchase.held) return <TrustBadge trust={purchase.trust} gap={purchase.gap} held />;
  const status = <Badge tone={purchaseStatusTone[purchase.status]}>{purchaseStatusLabel[purchase.status]}</Badge>;
  // A receipt's reading beside its status, so the drafts that need care stand
  // out before they are opened (issue 122); nothing for one entered by hand.
  if (!purchase.trust || purchase.status === "voided") return status;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {status}
      <TrustBadge trust={purchase.trust} gap={purchase.gap} />
    </span>
  );
}
