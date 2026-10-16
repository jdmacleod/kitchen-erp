import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import {
  useOlderRules,
  useRecheckOlder,
  type OlderReady,
  type OlderRules,
} from "../../api/rereads";
import { useNotice } from "../../components/Notice";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  focusRing,
  PageHeader,
  secondaryLinkClass,
  sectionHeading,
} from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-sm text-neutral-600 dark:text-neutral-400";

function drafts(n: number): string {
  return `${n} ${n === 1 ? "draft" : "drafts"}`;
}

/** "2 changed, 1 added": the proposal's counts, leaving out the zeros. */
function changeSentence(counts: OlderReady["counts"]): string {
  const parts = (["changed", "added", "removed"] as const)
    .filter((k) => counts[k] > 0)
    .map((k) => `${counts[k]} ${k}`);
  return parts.length ? parts.join(", ") : "No line changes";
}

/** "Re-checked 5 drafts: 2 would change, 3 already match." */
function doneSentence(checked: NonNullable<OlderRules["checked"]>): string {
  const total = checked.ready + checked.no_changes + checked.skipped;
  const parts = [
    checked.ready ? `${checked.ready} would change` : null,
    checked.no_changes
      ? `${checked.no_changes} already ${checked.no_changes === 1 ? "matches" : "match"}`
      : null,
    checked.skipped ? `${checked.skipped} couldn't be re-checked` : null,
  ].filter((p): p is string => p !== null);
  if (total === 0) return "No draft needed a re-check.";
  return `Re-checked ${drafts(total)}: ${parts.join(", ")}.`;
}

function Breadcrumb() {
  return (
    <nav aria-label="Breadcrumb" className={`mb-2 text-sm ${muted}`}>
      <span>Shop</span> <span aria-hidden="true">/</span>{" "}
      <Link
        to="/shop/receipts"
        className={`inline-flex min-h-11 items-center rounded-sm underline lg:min-h-0 ${focusRing}`}
      >
        Receipts
      </Link>
    </nav>
  );
}

function ReadyRow({ item }: { item: OlderReady }) {
  const day = formatDate(item.purchased_at);
  const title = item.store ? `${item.store} receipt from ${day}` : `Receipt from ${day}`;
  const after = item.adds_up
    ? "Adds up after"
    : item.off_by
      ? `Still off by ${formatMoney(item.off_by)} after`
      : null;
  return (
    <li>
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <span className="min-w-0">
          <span className="block font-medium">{title}</span>
          <span className={`block ${muted}`}>
            {changeSentence(item.counts)}
            {after ? ` · ${after}` : null}
          </span>
        </span>
        <Link
          to={`/shop/purchases/${item.purchase_id}/reread`}
          className={secondaryLinkClass}
          aria-label={`Review the re-read of the ${title}`}
        >
          Review
        </Link>
      </Card>
    </li>
  );
}

/**
 * Re-check older readings (`/shop/receipts/recheck`; 10; issue 382). Reached
 * from the inbox row "n drafts were read with older rules". One button runs the
 * check-mode re-read on each of them, with no model; the drafts whose lines
 * would change are listed, each opening its proposal. Nothing is applied here.
 */
export function RecheckPage() {
  usePageTitle("Re-check older readings");
  const found = useOlderRules();
  const recheck = useRecheckOlder();
  const notice = useNotice();
  const older = found.data?.older ?? 0;
  const ready = found.data?.ready ?? [];

  const onRecheck = () =>
    recheck.mutate(undefined, {
      onSuccess: (done) => {
        if (done.checked) notice.show({ tone: "success", message: doneSentence(done.checked) });
      },
    });

  return (
    <>
      <Breadcrumb />
      <PageHeader
        title="Re-check older readings"
        description="Drafts read before today's line rules. A re-check runs them on the same reading, instantly and with no model. Nothing changes until you apply a re-read."
      >
        {older > 0 ? (
          <Button disabled={recheck.isPending} onClick={onRecheck}>
            {recheck.isPending ? "Re-checking…" : `Re-check ${drafts(older)}`}
          </Button>
        ) : null}
      </PageHeader>
      {recheck.isError ? <Alert tone="error">{errorMessage(recheck.error)}</Alert> : null}
      {found.isPending ? (
        <p role="status" className={muted}>
          Loading…
        </p>
      ) : found.isError ? (
        <Alert tone="error">{errorMessage(found.error)}</Alert>
      ) : older === 0 && ready.length === 0 ? (
        <EmptyState
          title="No draft waits for a re-check"
          action={
            <Link to="/" className={secondaryLinkClass}>
              Back to Home
            </Link>
          }
        >
          Every draft was read, or re-checked, with today's line rules.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-4">
          {older > 0 ? (
            <p className={muted}>
              {drafts(older)} {older === 1 ? "was" : "were"} read with older rules. Committed
              purchases are left out: re-read those one at a time from their page.
            </p>
          ) : null}
          {ready.length > 0 ? (
            <section aria-labelledby="recheck-ready" className="flex flex-col gap-3">
              <h2 id="recheck-ready" className={sectionHeading}>
                Would change
              </h2>
              <p className={muted}>
                Open each to see its lines before and after, then apply or discard it.
              </p>
              <ul aria-label="Drafts whose lines would change" className="flex flex-col gap-3">
                {ready.map((item) => (
                  <ReadyRow key={item.reread_id} item={item} />
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      )}
    </>
  );
}
