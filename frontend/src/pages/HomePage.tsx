import { Link } from "react-router";
import { errorMessage } from "../api/client";
import { useLocations } from "../api/geo";
import { useNeedsBridge } from "../api/pricebook";
import { useRecentCommittedPurchases, useToIdentify } from "../api/purchases";
import { FirstRunChecklist } from "../components/landing/FirstRunChecklist";
import { Lately } from "../components/landing/Lately";
import { NeedsYou, type QueueSummary } from "../components/landing/NeedsYou";
import { Alert, PageHeader, focusRing } from "../components/ui";
import { usePageTitle } from "../lib/usePageTitle";

/**
 * The landing route, in two modes.
 *
 * Until the household has a committed purchase it is the first-run checklist;
 * after that it is the home page proper — what needs you, then what you did.
 * There is no redirect in either direction, so a set-up household never lands on
 * a finished to-do list and a new one never lands on a form it cannot submit.
 *
 * Both facts come from endpoints the app already calls, deliberately:
 *
 *   hasLocation  GET /vendor-locations   already filters VendorLocation.active
 *                                        AND Vendor.active, which is exactly what
 *                                        LocationGuard gates the entry forms on.
 *                                        The two cannot disagree, because it is
 *                                        the same query under the same cache key.
 *   hasPurchase  GET /purchases?status=committed  see useRecentCommittedPurchases.
 *
 * Loading and errored are both distinct from empty. An empty array is also what a
 * query hands back while it is still asking and when it could not ask at all, so
 * neither state is allowed to render the checklist: telling an established
 * household to go and add a shop during an outage is the one thing this page must
 * never do.
 *
 * All four queries start together. Gating the two queues behind "setup complete"
 * would make the common case — every login, forever — a two-step waterfall, to
 * save a brand-new deployment two empty responses exactly once.
 */
export function HomePage() {
  const locations = useLocations({});
  const recent = useRecentCommittedPurchases();
  const toIdentify = useToIdentify();
  const needsBridge = useNeedsBridge();

  const pending = locations.isPending || recent.isPending;
  const failed = locations.isError || recent.isError;
  const hasLocation = locations.isSuccess && locations.data.length > 0;
  const hasPurchase = recent.isSuccess && recent.data.length > 0;
  const firstRun = !pending && !failed && !hasPurchase;

  usePageTitle(firstRun ? "Set up your kitchen" : "Home");

  // Errored is checked before pending, not after. One lookup failing while the
  // other is still in flight is a known answer, and hiding it behind "Loading…"
  // leaves someone watching a spinner that will never resolve into anything but
  // this error.
  if (failed) {
    return (
      <>
        <PageHeader title="Home" />
        <Alert tone="error">{errorMessage(locations.error ?? recent.error)}</Alert>
      </>
    );
  }

  // No heading while the answers are in flight. The two modes have different
  // headings, so committing to one here would flash "Home" at a brand-new owner
  // and then replace it with "Set up your kitchen" — on the exact screen this
  // page exists to make calm. The status line carries the wait instead.
  if (pending) {
    return (
      <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
        Loading…
      </p>
    );
  }

  if (firstRun) {
    return (
      <>
        <PageHeader title="Set up your kitchen" />
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Kitchen ERP records what you buy, where, and for how much, so the price book can
          tell you where a thing is cheapest. Two things to do before it has anything to
          work with.
        </p>
        <FirstRunChecklist hasLocation={hasLocation} />
      </>
    );
  }

  // Fixed order, upstream first: a line nobody has identified yet cannot have a
  // bridge, so identifying comes before bridging. Order never depends on counts,
  // so a row does not move between visits.
  const queues: QueueSummary[] = [
    {
      id: "the to-identify queue",
      label: (n) => `${n} receipt ${n === 1 ? "line" : "lines"} to identify`,
      to: "/to-identify",
      linkLabel: "Identify",
      // Groups, not lines: the queue groups identical raw text per vendor, and the
      // row promises lines, so sum what each group actually holds.
      count: toIdentify.data?.reduce((n, g) => n + g.line_count, 0) ?? 0,
      isPending: toIdentify.isPending,
      isError: toIdentify.isError,
    },
    {
      id: "the needs-a-bridge queue",
      label: (n) => `${n} ${n === 1 ? "product needs" : "products need"} a unit bridge`,
      to: "/price-book/needs-bridge",
      linkLabel: "Add bridges",
      // Distinct products, not rows. `needs_bridge` groups by norm_status as well
      // as product (pricebook.py:262-263), so one product failing two ways is two
      // rows, and counting rows would overstate the work waiting.
      count: new Set(needsBridge.data?.map((item) => item.product.id) ?? []).size,
      isPending: needsBridge.isPending,
      isError: needsBridge.isError,
    },
  ];

  return (
    <>
      <PageHeader title="Home">
        <Link
          to="/purchases/new"
          className={`inline-flex min-h-10 items-center rounded-md bg-blue-600 px-3 text-sm font-medium text-white hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-400 ${focusRing}`}
        >
          New purchase
        </Link>
      </PageHeader>
      <NeedsYou queues={queues} />
      <Lately purchases={recent.data} />
    </>
  );
}
