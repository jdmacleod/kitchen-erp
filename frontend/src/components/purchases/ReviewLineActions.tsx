import { useEffect, useRef, useState } from "react";
import type { PurchaseLine } from "../../api/purchases";
import { Button } from "../ui";

export interface LineActionsProps {
  line: PurchaseLine;
  picking: boolean;
  supplying: boolean;
  editing: boolean;
  busy: boolean;
  onOpenPicker: () => void;
  onIgnore: () => void;
  onOpenSupply: () => void;
  onReResolve: () => void;
  onEdit: () => void;
  onDelete: () => void;
  touch?: boolean;
}

/**
 * Choose, Ignore, Kitchen supply, Re-resolve, Edit and Delete. 44px on a card,
 * compact in the table. Kitchen supply opens its choices where the picker opens
 * (spec 04, 2T).
 */
export function LineActions({
  line,
  picking,
  supplying,
  editing,
  busy,
  onOpenPicker,
  onIgnore,
  onOpenSupply,
  onReResolve,
  onEdit,
  onDelete,
  touch,
}: LineActionsProps) {
  const size = touch
    ? "min-h-11 px-3 text-sm"
    : "min-h-11 lg:min-h-7 px-2 text-xs whitespace-nowrap";
  // A line that reached the price book asks first: deleting it voids its
  // price (#72, D12). One that never did still goes in one click.
  const [confirming, setConfirming] = useState(false);
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) keep.current?.focus();
  }, [confirming]);
  if (confirming) {
    return (
      <div
        role="group"
        aria-label={`Delete line ${line.seq}`}
        className="flex flex-wrap items-center gap-2 rounded-md border border-red-300 bg-red-50 p-2 dark:border-red-900 dark:bg-red-950"
      >
        <span className="text-sm">Delete line {line.seq}? Its price is voided.</span>
        <Button variant="danger" className={size} disabled={busy} onClick={onDelete}>
          Delete
        </Button>
        <Button
          ref={keep}
          variant="secondary"
          className={size}
          onClick={() => {
            setConfirming(false);
            document.getElementById(`review-line-${line.id}`)?.focus();
          }}
        >
          Keep it
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-1">
      {line.line_kind === "item" ? (
        <>
          <Button
            variant="secondary"
            className={size}
            disabled={busy || (picking && !supplying)}
            onClick={onOpenPicker}
          >
            {line.product ? "Change" : "Choose…"}
          </Button>
          {line.resolution !== "ignored" ? (
            <Button variant="secondary" className={size} disabled={busy} onClick={onIgnore}>
              Ignore
            </Button>
          ) : null}
          <Button
            variant="secondary"
            // It may wrap: on one line it is wider than the table's Actions column.
            className={touch ? size : "min-h-11 lg:min-h-7 px-2 text-xs"}
            disabled={busy || supplying}
            onClick={onOpenSupply}
          >
            Kitchen supply
          </Button>
          <Button variant="ghost" className={size} disabled={busy} onClick={onReResolve}>
            Re-resolve
          </Button>
        </>
      ) : null}
      <Button variant="ghost" className={size} disabled={busy || editing} onClick={onEdit}>
        Edit
      </Button>
      <Button
        variant="ghost"
        className={`${size} text-red-700 dark:text-red-300`}
        disabled={busy}
        onClick={line.recorded ? () => setConfirming(true) : onDelete}
        aria-label={`Delete line ${line.seq}`}
      >
        Delete
      </Button>
    </div>
  );
}
