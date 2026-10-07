import { useEffect, useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  purchaseErrorMessage,
  purchaseKeys,
  useRemovePurchase,
  useRestorePurchase,
  type Purchase,
  type RemovedPurchase,
} from "../../api/purchases";
import { isApiError } from "../../api/client";
import { formatDate } from "../../lib/format";
import { alertTones, Button, focusRing } from "../ui";

/**
 * "Remove this purchase", at the foot of the purchase page (#74; spec 10,
 * Removing lines and purchases). What it says comes from the server's
 * `removal` preview, the same rule the removal itself follows, so the page
 * never guesses whether the purchase will be deleted or voided.
 */
export function RemovePurchase({ purchase, onRemoved }: { purchase: Purchase; onRemoved: (removed: RemovedPurchase) => void }) {
  const removal = purchase.removal;
  const remove = useRemovePurchase(purchase.id);
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const headingId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const alert = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (confirming) keep.current?.focus();
  }, [confirming]);
  useEffect(() => {
    if (remove.isError) alert.current?.focus();
  }, [remove.isError, remove.failureCount]);

  if (!removal) return null;
  // The receipt started being read again after the page loaded. Say so, and
  // leave Remove usable: the refreshed preview decides whether it is blocked.
  const raced = remove.isError && isApiError(remove.error) && remove.error.code === "still_reading";
  const reading = removal.blocked === "still_reading";
  const outcome = outcomeSentence(purchase);

  const cancel = () => {
    setConfirming(false);
    remove.reset();
    // Back to the button that opened it, once it is in the document again.
    requestAnimationFrame(() => trigger.current?.focus());
  };

  return (
    <section
      aria-labelledby={`${headingId}-section`}
      // Below lg the sticky Commit bar and the tab bar sit over the page's
      // foot; the padding lets this section scroll fully clear of both (D19).
      className="mt-2 border-t border-neutral-200 pt-6 pb-[calc(11rem+env(safe-area-inset-bottom))] lg:pb-0 dark:border-neutral-800"
    >
      <h2 id={`${headingId}-section`} className="text-lg font-medium">
        Remove this purchase
      </h2>
      {reading || raced ? (
        <p className="mt-1 text-sm text-neutral-700 dark:text-neutral-300">You can remove it once it&apos;s been read.</p>
      ) : (
        <p className="mt-1 text-sm text-neutral-700 dark:text-neutral-300">{outcome}</p>
      )}
      {confirming && !reading ? (
        <div
          role="group"
          aria-labelledby={headingId}
          className="mt-3 flex flex-col gap-3 rounded-md border border-red-300 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950"
        >
          <h3 id={headingId} className="text-sm font-medium">
            {confirmHeading(purchase)}
          </h3>
          <p className="text-sm">{outcome}</p>
          {remove.isError ? (
            <div ref={alert} tabIndex={-1} role="alert" className={`rounded-md border px-3 py-2 text-sm ${alertTones.error} ${focusRing}`}>
              {purchaseErrorMessage(remove.error)}
            </div>
          ) : null}
          <div className="flex flex-col gap-2 lg:flex-row">
            <Button
              variant="dangerFill"
              className="w-full lg:w-auto"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(undefined, {
                  onSuccess: onRemoved,
                  onError: (e) => {
                    if (!isApiError(e) || e.code !== "still_reading") return;
                    // Close the confirm and ask the server again what removal would do.
                    setConfirming(false);
                    void client.invalidateQueries({ queryKey: purchaseKeys.purchase(purchase.id) });
                  },
                })
              }
            >
              {remove.isPending ? "Removing…" : "Remove purchase"}
            </Button>
            <Button ref={keep} variant="secondary" className="w-full lg:w-auto" disabled={remove.isPending} onClick={cancel}>
              Keep it
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <Button
            ref={trigger}
            variant="danger"
            className="w-full lg:w-auto"
            disabled={reading}
            onClick={() => {
              remove.reset();
              setConfirming(true);
            }}
          >
            Remove purchase
          </Button>
        </div>
      )}
    </section>
  );
}

/**
 * "Restore this purchase", at the foot of a removed (voided) purchase (issue 210;
 * spec 10, Removing lines and purchases). Restoring puts it back in review; its
 * voided prices stay voided, and committing it records them again.
 */
export function RestorePurchase({ purchase, onRestored }: { purchase: Purchase; onRestored: (restored: Purchase) => void }) {
  const restore = useRestorePurchase(purchase.id);
  const [confirming, setConfirming] = useState(false);
  const headingId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const alert = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (confirming) keep.current?.focus();
  }, [confirming]);
  useEffect(() => {
    if (restore.isError) alert.current?.focus();
  }, [restore.isError, restore.failureCount]);

  if (purchase.status !== "voided") return null;
  const blocked = purchase.restore_blocked === "read_again";
  const cancel = () => {
    setConfirming(false);
    restore.reset();
    requestAnimationFrame(() => trigger.current?.focus());
  };

  return (
    <section aria-labelledby={`${headingId}-section`} className="mt-2 border-t border-neutral-200 pt-6 pb-[calc(6rem+env(safe-area-inset-bottom))] lg:pb-0 dark:border-neutral-800">
      <h2 id={`${headingId}-section`} className="text-lg font-medium">
        Restore this purchase
      </h2>
      <p className="mt-1 text-sm text-neutral-700 dark:text-neutral-300">
        {blocked
          ? "Its receipt was uploaded again, so it already has a newer purchase. This one stays removed."
          : "Restoring brings it back to review. Its prices count again once you commit it."}
      </p>
      {blocked ? null : confirming ? (
        <div role="group" aria-labelledby={headingId} className="mt-3 flex flex-col gap-3 rounded-md border border-neutral-200 p-3 dark:border-neutral-800">
          <h3 id={headingId} className="text-sm font-medium">
            {restoreHeading(purchase)}
          </h3>
          {restore.isError ? (
            <div ref={alert} tabIndex={-1} role="alert" className={`rounded-md border px-3 py-2 text-sm ${alertTones.error} ${focusRing}`}>
              {purchaseErrorMessage(restore.error)}
            </div>
          ) : null}
          <div className="flex flex-col gap-2 lg:flex-row">
            <Button className="w-full lg:w-auto" disabled={restore.isPending} onClick={() => restore.mutate(undefined, { onSuccess: onRestored })}>
              {restore.isPending ? "Restoring…" : "Restore purchase"}
            </Button>
            <Button ref={keep} variant="secondary" className="w-full lg:w-auto" disabled={restore.isPending} onClick={cancel}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <Button
            ref={trigger}
            variant="secondary"
            className="w-full lg:w-auto"
            onClick={() => {
              restore.reset();
              setConfirming(true);
            }}
          >
            Restore purchase
          </Button>
        </div>
      )}
    </section>
  );
}

/** "Restore the {vendor} purchase from {date}?", or the receipt form. */
function restoreHeading(purchase: Purchase): string {
  const date = formatDate(purchase.purchased_at);
  const where = purchase.vendor_location?.vendor.name ?? purchase.vendor_location?.name;
  return where ? `Restore the ${where} purchase from ${date}?` : `Restore this ${date} receipt?`;
}

/** What removing will do, in the words spec 10 gives (D15). */
export function outcomeSentence(purchase: Purchase): string {
  const removal = purchase.removal;
  if (!removal) return "";
  if (removal.outcome === "void") {
    const n = removal.prices;
    // Every price it had was voided already, by a reopen or a removed line.
    if (n === 0) return "It's been in the price book, so it will be kept as voided. You can restore it afterwards.";
    return `It's in the price book, so its ${n} ${n === 1 ? "price" : "prices"} will be voided. You can restore it afterwards.`;
  }
  return removal.photo
    ? "Nothing from it reached the price book, so it will be deleted, along with its receipt photo."
    : "Nothing from it reached the price book, so it will be deleted.";
}

/** "Remove the {vendor or location} purchase from {date}?", or the receipt form (D16). */
function confirmHeading(purchase: Purchase): string {
  const date = formatDate(purchase.purchased_at);
  const where = purchase.vendor_location?.vendor.name ?? purchase.vendor_location?.name;
  return where ? `Remove the ${where} purchase from ${date}?` : `Remove this ${date} receipt?`;
}

/** "{n} line(s) removed", under the lines table (D14). */
export function RemovedLinesCaption({ purchase }: { purchase: Purchase }) {
  const n = purchase.removed_line_count ?? 0;
  if (n === 0) return null;
  return (
    <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
      {n} {n === 1 ? "line" : "lines"} removed
    </p>
  );
}
