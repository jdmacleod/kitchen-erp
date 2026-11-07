import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import {
  useDecideOffer,
  useOfferQueue,
  type OfferDecision,
  type OfferGroup,
} from "../../api/flyerOffers";
import { hintClass } from "../../components/catalog/fields";
import { useNotice } from "../../components/Notice";
import { ProductPicker } from "../../components/purchases/ProductPicker";
import { SegmentedControl } from "../../components/SegmentedControl";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  PageHeader,
  secondaryLinkClass,
} from "../../components/ui";
import { stripZeros } from "../../lib/decimal";
import { formatCalendarDate } from "../../lib/format";
import { offerPriceText } from "../../lib/offers";
import { usePageTitle } from "../../lib/usePageTitle";

type Tab = "watched" | "other";

function groupKey(g: OfferGroup | undefined): string | null {
  return g ? `${g.vendor.id}:${g.name_norm}` : null;
}

/**
 * This week's flyer offers that need a product (18, 2U-3; UI-9.3, UI-9.6). Watched
 * ingredients' offers ask here and in the inbox; every other suggestion waits under
 * "Other offers", to match when you like (F13). One answer covers every store's
 * offer of that printed name, and the name is learned for next week.
 */
export function OfferQueuePage() {
  usePageTitle("Match offers");
  const queue = useOfferQueue();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "other" ? "other" : "watched";
  const groups = queue.data?.[tab] ?? [];
  const setTab = (next: Tab) =>
    setParams(next === "watched" ? {} : { tab: next }, { replace: true });

  return (
    <>
      <PageHeader
        title="Match offers"
        description="This week's flyer offers without a product yet. Match a name once and next week's links on its own."
      />
      {queue.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : queue.isError ? (
        <Alert tone="error">{errorMessage(queue.error)}</Alert>
      ) : (
        <>
          <div className="mb-4">
            <SegmentedControl
              label="Offers"
              options={[
                { value: "watched", label: `Watched ${queue.data.watched.length}` },
                { value: "other", label: `Other offers ${queue.data.other.length}` },
              ]}
              value={tab}
              onChange={setTab}
            />
          </div>
          {groups.length === 0 ? (
            <EmptyState
              title={tab === "watched" ? "No watched offers to match" : "No other offers waiting"}
              action={
                <Link to="/" className={secondaryLinkClass}>
                  Back to Home
                </Link>
              }
            >
              {tab === "watched"
                ? "Watch an ingredient from its page, and its offers ask here."
                : "Every other offer this week has a product or isn't wanted."}
            </EmptyState>
          ) : (
            <GroupList groups={groups} tab={tab} />
          )}
        </>
      )}
    </>
  );
}

function GroupList({ groups, tab }: { groups: OfferGroup[]; tab: Tab }) {
  // An answered group leaves the list; the next one's product box takes focus, as
  // on To identify (#88).
  const focusKey = useRef<string | null>(null);
  useEffect(() => {
    const key = focusKey.current;
    if (key === null) return;
    focusKey.current = null;
    const at = groups.findIndex((g) => groupKey(g) === key);
    // Its product box, or the ingredient box of the new product it is being given.
    if (at >= 0)
      (
        document.getElementById(`offer-${at}`) ??
        document.getElementById(`offer-${at}-new-ingredient`)
      )?.focus();
  });
  return (
    <ul
      aria-label={tab === "watched" ? "Watched offers" : "Other offers"}
      className="flex flex-col gap-4"
    >
      {groups.map((g, i) => (
        <li key={groupKey(g)}>
          <Card>
            <OfferCard
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
  );
}

function OfferCard({
  group,
  index,
  onAnswered,
}: {
  group: OfferGroup;
  index: number;
  onAnswered: () => void;
}) {
  const decide = useDecideOffer();
  const notice = useNotice();
  const id = `offer-${index}`;
  const answer = (choice: { product_id: string } | { ignore: true }, what: string) => {
    const input: OfferDecision = {
      vendor_id: group.vendor.id,
      name_norm: group.name_norm,
      ...choice,
    };
    decide.mutate(input, {
      onSuccess: ({ decided }) => {
        notice.show({
          tone: "success",
          message: `${what} for ${decided} ${decided === 1 ? "offer" : "offers"}.`,
        });
        onAnswered();
      },
    });
  };
  const products = group.suggestions.flatMap((s) =>
    s.product_id && !s.ignore ? [{ id: s.product_id, label: s.label }] : [],
  );

  return (
    <div
      className="flex flex-col gap-3"
      role="group"
      aria-label={`${group.vendor.name}: ${group.name_as_printed}`}
    >
      <h2 className="text-base font-medium">
        {group.name_as_printed}{" "}
        <span className="font-normal text-neutral-600 dark:text-neutral-400">
          · {group.vendor.name}
        </span>
      </h2>
      {decide.error ? <Alert tone="error">{errorMessage(decide.error)}</Alert> : null}
      <ul className="flex flex-col gap-1 text-sm text-neutral-700 dark:text-neutral-300">
        {group.offers.map((o) => (
          <li key={o.id}>
            <span className="font-medium text-neutral-900 dark:text-neutral-100">
              {offerPriceText(o)}
            </span>
            {o.size_qty && o.size_unit ? ` · ${stripZeros(o.size_qty)} ${o.size_unit}` : ""} ·{" "}
            {o.location.name} · through {formatCalendarDate(o.valid_to)}
            {o.name_as_printed !== group.name_as_printed ? (
              <span className={hintClass}> ({o.name_as_printed})</span>
            ) : null}
          </li>
        ))}
      </ul>
      {products.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className={hintClass}>Maybe:</span>
          {products.map((s) => (
            <Button
              key={s.id}
              variant="secondary"
              disabled={decide.isPending}
              onClick={() => answer({ product_id: s.id }, `Used ${s.label}`)}
            >
              Use {s.label}
            </Button>
          ))}
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
        <ProductPicker
          id={id}
          label="Product"
          value={null}
          lineText={group.name_as_printed}
          onChange={(p) => {
            if (p) answer({ product_id: p.id }, `Used ${p.name}`);
          }}
          disabled={decide.isPending}
        />
        <div className="flex flex-wrap items-end gap-2">
          <Button
            variant="secondary"
            disabled={decide.isPending}
            onClick={() => answer({ ignore: true }, "Not wanted")}
          >
            Not wanted
          </Button>
        </div>
      </div>
    </div>
  );
}
