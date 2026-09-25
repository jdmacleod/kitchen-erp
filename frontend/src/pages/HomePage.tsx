import { errorMessage } from "../api/client";
import { useLocations } from "../api/geo";
import { useInbox } from "../api/inbox";
import { useRecentCommittedPurchases } from "../api/purchases";
import { useChrome } from "../components/chrome";
import { FirstRunChecklist } from "../components/landing/FirstRunChecklist";
import { InboxList, ReadingLine, useInboxSummary } from "../components/landing/InboxList";
import { RecentPurchases } from "../components/landing/RecentPurchases";
import { Alert, Button, PageHeader } from "../components/ui";
import { usePageTitle } from "../lib/usePageTitle";

/** A time-of-day greeting without a name (G12). */
export function greeting(hour = new Date().getHours()): string {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/**
 * The landing route, in two modes (docs/spec/10, Home).
 *
 * Until the household has a committed purchase it is the first-run checklist;
 * after that it is Home proper: what needs you, then what you did. Inbox rows
 * render above the checklist whenever there are any (G7), because a receipt can
 * be waiting before anything was ever committed.
 *
 * The first-run decision comes from endpoints the app already calls:
 *
 *   hasLocation  GET /vendor-locations   already filters VendorLocation.active
 *                                        AND Vendor.active, which is exactly what
 *                                        LocationGuard gates the entry forms on.
 *   hasPurchase  GET /purchases?status=committed  see useRecentCommittedPurchases.
 *
 * Loading and errored are both distinct from empty. An empty array is also what a
 * query hands back while it is still asking and when it could not ask at all, so
 * neither state is allowed to render the checklist: telling an established
 * household to go and add a shop during an outage is the one thing this page must
 * never do. The inbox keeps its own states for the same reason (D6).
 */
export function HomePage() {
  const locations = useLocations({});
  const recent = useRecentCommittedPurchases();
  const inbox = useInbox();
  const summary = useInboxSummary();
  const { openCapture } = useChrome();

  const pending = locations.isPending || recent.isPending;
  const failed = locations.isError || recent.isError;
  const hasLocation = locations.isSuccess && locations.data.length > 0;
  const hasPurchase = recent.isSuccess && recent.data.length > 0;
  const firstRun = !pending && !failed && !hasPurchase;

  usePageTitle(firstRun ? "Set up your kitchen" : "Home");

  // No heading while the answers are in flight. The two modes have different
  // headings, so committing to one here would flash a greeting at a brand-new
  // owner and then replace it with "Set up your kitchen", on the exact screen
  // this page exists to make calm. An error is checked first: it is a known
  // answer, and it can only mean the household is not new.
  if (pending && !failed) {
    return (
      <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
        Loading…
      </p>
    );
  }

  if (firstRun) {
    const rows = inbox.data?.items.length ?? 0;
    return (
      <>
        <PageHeader title="Set up your kitchen" />
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Kitchen ERP records what you buy, where, and for how much, so the price book can
          tell you where a thing is cheapest. Two things to do before it has anything to
          work with.
        </p>
        {/* Only rows, not the empty state: on a new kitchen "Nothing needs you" above
            a to-do list would contradict it. Errors still show. */}
        {rows > 0 || inbox.isError ? (
          <section aria-label="Needs you" className="mt-6">
            <ReadingLine />
            <InboxList />
          </section>
        ) : (
          <div className="mt-6">
            <ReadingLine />
          </div>
        )}
        <FirstRunChecklist hasLocation={hasLocation} />
      </>
    );
  }

  return (
    <>
      <PageHeader title={greeting()}>
        <Button onClick={openCapture}>Capture</Button>
      </PageHeader>
      <p className="-mt-4 mb-6 min-h-5 text-sm text-neutral-600 dark:text-neutral-400">{summary}</p>

      <div className="grid gap-8 lg:grid-cols-[1.65fr_1fr]">
        <section aria-labelledby="needs-you">
          <h2 id="needs-you" className="font-display mb-3 text-lg">
            Needs you
          </h2>
          <ReadingLine />
          <InboxList />
        </section>
        <div>
          {failed ? (
            <Alert tone="error">{errorMessage(locations.error ?? recent.error)}</Alert>
          ) : (
            <RecentPurchases purchases={recent.data ?? []} />
          )}
        </div>
      </div>
    </>
  );
}
