import { useState } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import type { InboxItem, InboxKind } from "../../api/inbox";
import { readingSentence, readingTotal, useInbox } from "../../api/inbox";
import { formatTime } from "../../lib/format";
import { ingestErrorText } from "../../lib/ingestErrors";
import { Badge, type BadgeTone } from "../catalog/fields";
import { TrustBadge } from "../purchases/TrustBadge";
import { useChrome } from "../chrome";
import { Alert, Button, Card, focusRing, secondaryLinkClass, tapTarget } from "../ui";

const kinds: Record<InboxKind, { label: string; tone: BadgeTone }> = {
  receipt: { label: "Receipt", tone: "neutral" },
  // Squash, not tomato: nothing failed, but its lines are far off its total (issue 121).
  receipt_held: { label: "Careful look", tone: "warn" },
  // The one kind in tomato: a read that failed is the only row about an error (G5).
  receipt_failed: { label: "Couldn't read", tone: "danger" },
  identify: { label: "Identify", tone: "neutral" },
  bridge: { label: "Bridge", tone: "neutral" },
  vendor_suggestions: { label: "Vendors", tone: "neutral" },
  link: { label: "Link", tone: "neutral" },
  usda: { label: "USDA", tone: "neutral" },
  new_product: { label: "Products", tone: "neutral" },
  product_update: { label: "Products", tone: "neutral" },
  posted_prices: { label: "Prices", tone: "neutral" },
};

/** Rows shown on a phone before "See all" (G6). */
const PHONE_ROWS = 3;

function Row({ item, phoneHidden }: { item: InboxItem; phoneHidden: boolean }) {
  const kind = kinds[item.kind] ?? { label: item.kind, tone: "neutral" as const };
  const error = item.error_code ? ingestErrorText(item.error_code).message : null;
  return (
    <li
      data-testid="inbox-item"
      className={`grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2 border-t border-neutral-200 py-3 first:border-t-0 sm:col-span-3 sm:grid-cols-subgrid dark:border-neutral-800 ${
        phoneHidden ? "max-lg:hidden" : ""
      }`}
    >
      <span>
        <Badge tone={kind.tone}>{kind.label}</Badge>
      </span>
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-x-2 font-medium">
          {item.title}
          {/* A receipt's reading, before it is opened (issue 122). */}
          {item.kind === "receipt" ? <TrustBadge trust={item.trust} gap={item.gap} /> : null}
        </span>
        <span className="block text-sm text-neutral-600 dark:text-neutral-400">
          {error ? `${error} ` : null}
          {item.detail}
        </span>
      </span>
      <Link to={item.action_route} className={`${secondaryLinkClass} col-span-2 sm:col-span-1`}>
        {item.action_label}
      </Link>
    </li>
  );
}

/**
 * Needs you: everything the system could not finish on its own, oldest first
 * (docs/spec/09, Unified inbox; 10, Home).
 *
 * An error is never shown as an empty inbox (D6): "Nothing needs you" is a claim,
 * and during an outage it would be false.
 */
export function InboxList() {
  const inbox = useInbox();
  const { openCapture } = useChrome();
  const [expanded, setExpanded] = useState(false);

  if (inbox.isPending) {
    return (
      <Card>
        <p role="status" className="sr-only">
          Loading what needs you…
        </p>
        <ul aria-hidden="true" className="flex flex-col gap-3">
          {[0, 1, 2].map((i) => (
            <li key={i} className="h-12 animate-pulse rounded-md bg-neutral-100 dark:bg-neutral-800" />
          ))}
        </ul>
      </Card>
    );
  }

  if (inbox.isError) {
    return (
      <Alert tone="error">
        <span className="flex flex-wrap items-center justify-between gap-2">
          <span>Couldn&apos;t load what needs you. {errorMessage(inbox.error)}</span>
          <Button variant="secondary" disabled={inbox.isFetching} onClick={() => void inbox.refetch()}>
            Try again
          </Button>
        </span>
      </Alert>
    );
  }

  const items = inbox.data.items;
  if (items.length === 0) {
    return (
      <Card>
        <p className="font-medium">Nothing needs you</p>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
          Receipts to finish, lines to identify and prices that can&apos;t be compared show up here.
        </p>
        <Button variant="secondary" className="mt-3" onClick={openCapture}>
          Capture
        </Button>
      </Card>
    );
  }

  const more = items.length - PHONE_ROWS;
  return (
    <Card>
      {/* One column for the kind badges, as wide as the widest one shown: no fixed slot (UI-6.1, issue 247). */}
      <ul aria-label="Needs you" className="sm:grid sm:grid-cols-[max-content_1fr_auto] sm:gap-x-3">
        {items.map((item, i) => (
          <Row key={`${item.kind}-${item.action_route}-${item.created_at}`} item={item} phoneHidden={!expanded && i >= PHONE_ROWS} />
        ))}
      </ul>
      {more > 0 && !expanded ? (
        <Button variant="ghost" className="mt-2 w-full lg:hidden" onClick={() => setExpanded(true)}>
          See all {items.length}
        </Button>
      ) : null}
      <p className="mt-3 border-t border-neutral-200 pt-3 text-xs text-neutral-600 dark:border-neutral-800 dark:text-neutral-400">
        Anything the system can&apos;t finish on its own lands here, oldest first, and leaves once it&apos;s done.
      </p>
    </Card>
  );
}

/** Receipts and products being read: above Needs you, not counted in the badge (G1, D21, PD7). */
export function ReadingLine() {
  const inbox = useInbox();
  const reading = inbox.data?.reading;
  if (!reading) return null;
  const overdue = reading.lookups_overdue ?? 0;
  const lookupLine = overdue > 0 && reading.lookups_since ? (
    <p role="status" className="mb-3 rounded-md border border-amber-400 px-3 py-2 text-sm text-amber-900 dark:border-amber-600 dark:text-amber-200">
      {overdue} {overdue === 1 ? "lookup" : "lookups"} waiting for the lookup helper since {formatTime(reading.lookups_since)} ·{" "}
      <Link to="/settings/system" className={`${tapTarget} rounded font-medium underline ${focusRing}`}>
        Check System
      </Link>
    </p>
  ) : null;
  if (readingTotal(reading) === 0) return lookupLine;
  if (reading.stalled) {
    return (
      <>
      {lookupLine}
      <p role="status" className="mb-3 rounded-md border border-amber-400 px-3 py-2 text-sm text-amber-900 dark:border-amber-600 dark:text-amber-200">
        Reading is taking longer than usual ·{" "}
        <Link to="/settings/system" className={`${tapTarget} rounded font-medium underline ${focusRing}`}>
          Check System
        </Link>
      </p>
      </>
    );
  }
  return (
    <>
      {lookupLine}
      <p role="status" className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
        {readingSentence(reading)}
      </p>
    </>
  );
}

/** The header's one-line summary (G12); nothing until the inbox answers. */
export function useInboxSummary(): string | null {
  const inbox = useInbox();
  if (!inbox.isSuccess) return null;
  const n = inbox.data.items.length;
  if (n === 0) return "Nothing needs you";
  return `${n} ${n === 1 ? "thing needs" : "things need"} you`;
}
