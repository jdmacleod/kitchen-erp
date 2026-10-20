import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import { useToIdentify, type ToIdentifyGroup } from "../../api/toIdentify";
import { IdentifyFilters } from "../../components/purchases/IdentifyFilters";
import { GroupCard, RememberOffer, type Offer } from "../../components/purchases/IdentifyGroup";
import { filterGroups, readSort, storeCounts } from "../../components/purchases/identifyList";
import { NameProducts } from "../../components/purchases/NameProducts";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  PageHeader,
  secondaryLinkClass,
} from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";
import { useSearchText } from "../../lib/useSearchText";

/** Sets or clears address parameters together, replacing the history entry. */
type SetParams = (patch: Record<string, string | null>) => void;

/**
 * Unmatched lines across every committed purchase, grouped by vendor and
 * normalized text so one answer can settle every instance at once. Store,
 * wording and order narrow the list and live in the address, as does the mode.
 */
export function ToIdentifyPage() {
  usePageTitle("To identify");
  const queue = useToIdentify();
  const groups = queue.data ?? [];
  // Naming in bulk (N1): offered once a few groups wait, in the URL so Back leaves it.
  const [params, setParams] = useSearchParams();
  const naming = params.get("mode") === "name";
  const offerNaming = !naming && groups.length >= NAMING_MIN;
  const setParam: SetParams = (patch) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(patch)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        return next;
      },
      { replace: true },
    );
  // An answered group with an item code: the person may remember it (2K).
  const [offer, setOffer] = useState<Offer | null>(null);

  return (
    <>
      <PageHeader
        title="To identify"
        description="Receipt lines that don't have a product yet. Match one and the vendor's wording is learned."
      >
        {offerNaming ? (
          <Button onClick={() => setParam({ mode: "name" })}>Name the new products</Button>
        ) : null}
        {naming ? (
          <Button variant="secondary" onClick={() => setParam({ mode: null })}>
            One at a time
          </Button>
        ) : null}
      </PageHeader>
      {offer ? <RememberOffer offer={offer} onDone={() => setOffer(null)} /> : null}
      {naming ? (
        <NameProducts
          vendor={params.get("store") ?? ""}
          onVendor={(id) => setParam({ store: id || null })}
        />
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
        <QueueList groups={groups} params={params} setParam={setParam} onOffer={setOffer} />
      )}
    </>
  );
}

/** How many groups must wait before naming them in bulk is offered (N1). */
const NAMING_MIN = 3;
/** Groups shown at once; more on request. */
const PAGE = 25;

function groupKey(g: ToIdentifyGroup | undefined): string | null {
  return g ? `${g.vendor.id}:${g.raw_text_norm}` : null;
}

/** The filter bar and the groups it keeps, a page at a time. */
function QueueList({
  groups,
  params,
  setParam,
  onOffer,
}: {
  groups: ToIdentifyGroup[];
  params: URLSearchParams;
  setParam: SetParams;
  onOffer: (offer: Offer | null) => void;
}) {
  const store = params.get("store") ?? "";
  const q = params.get("q") ?? "";
  const sort = readSort(params.get("sort"));
  const [text, setText] = useSearchText(q, (v) => setParam({ q: v }));
  const listed = filterGroups(groups, { store, q, sort });
  const [shown, setShown] = useState(PAGE);
  // A new filter starts from the top of its list.
  const filterKey = `${store}|${q}|${sort}`;
  const [seenFilter, setSeenFilter] = useState(filterKey);
  if (filterKey !== seenFilter) {
    setSeenFilter(filterKey);
    setShown(PAGE);
  }
  const page = listed.slice(0, shown);
  // Once a group is answered it leaves the list, and the next one is where the
  // work continues: its product box takes focus (#88).
  const focusKey = useRef<string | null>(null);
  useEffect(() => {
    const key = focusKey.current;
    if (key === null) return;
    focusKey.current = null;
    const at = page.findIndex((g) => groupKey(g) === key);
    // Its product box, or the ingredient box of the new product it is being given.
    if (at >= 0)
      (
        document.getElementById(`identify-${at}`) ??
        document.getElementById(`identify-${at}-new-ingredient`)
      )?.focus();
  });
  const clear = () => {
    setText("");
    setParam({ q: null, store: null, sort: null });
  };

  return (
    <>
      <IdentifyFilters
        stores={storeCounts(groups)}
        store={store}
        onStore={(id) => setParam({ store: id || null })}
        text={text}
        onText={setText}
        sort={sort}
        onSort={(s) => setParam({ sort: s === "newest" ? null : s })}
      />
      {listed.length === 0 ? (
        <EmptyState
          title="No lines match"
          action={
            <Button variant="secondary" onClick={clear}>
              Clear filters
            </Button>
          }
        >
          {groups.length} {groups.length === 1 ? "group waits" : "groups wait"} in all.
        </EmptyState>
      ) : (
        <>
          <p role="status" className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
            Showing {page.length} of {listed.length} {listed.length === 1 ? "group" : "groups"}
            {listed.length < groups.length ? ` (${groups.length} in all)` : ""}
          </p>
          <ul aria-label="Lines to identify" className="flex flex-col gap-4">
            {page.map((g, i) => (
              <li key={groupKey(g)}>
                <Card>
                  <GroupCard
                    group={g}
                    index={i}
                    onAnswered={(next) => {
                      focusKey.current = groupKey(page[i + 1] ?? page[i - 1]);
                      onOffer(next);
                    }}
                    onWording={(wording) => {
                      setText(wording);
                      setParam({ q: wording, store: null });
                    }}
                  />
                </Card>
              </li>
            ))}
          </ul>
          {listed.length > page.length ? (
            <Button
              variant="secondary"
              className="mt-4 self-start"
              onClick={() => setShown(shown + PAGE)}
            >
              Show {Math.min(PAGE, listed.length - page.length)} more ({listed.length - page.length}{" "}
              waiting)
            </Button>
          ) : null}
        </>
      )}
    </>
  );
}
