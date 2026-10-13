import { useState } from "react";
import { Link } from "react-router";
import { formatPack } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useMe } from "../../api/queries";
import {
  itemName,
  useCheckItem,
  useDeleteItem,
  useUncheckItem,
  type ShoppingItem,
  type listKeys,
} from "../../api/shoppingList";
import { formatDateTime } from "../../lib/format";
import { Badge, QualityStars } from "../catalog/fields";
import { MoreActionsDialog } from "../MoreActionsDialog";
import { Alert, Button, focusRing } from "../ui";
import { formatAmounts } from "./amounts";
import { ClaimChip } from "./ClaimChip";
import { EditItemDrawer } from "./EditItemDrawer";

const muted = "text-neutral-600 dark:text-neutral-400";

interface ListItemRowProps {
  item: ShoppingItem;
  listKey: ReturnType<typeof listKeys.list>;
  /** An archived list: nothing on the row acts. */
  readOnly?: boolean;
  /** Told after a check or uncheck, for the live region. */
  onToggled?: (item: ShoppingItem, checked: boolean) => void;
  onError: (e: unknown) => void;
  onNotice: (message: string) => void;
}

/**
 * One item of the list (10, Plan: items): the 28px check circle with a 44px hit
 * area, the name with the product's pack in a caption, the amounts as written,
 * "deferrable" and the minimum quality when set, "from recipe" on recipe items,
 * the claim chip and the note. Edit and Remove live under More actions; Edit
 * opens a drawer with the unsaved-input guard.
 */
export function ListItemRow({
  item,
  listKey,
  readOnly = false,
  onToggled,
  onError,
  onNotice,
}: ListItemRowProps) {
  const me = useMe();
  // Hook-level callbacks: this row unmounts as it moves to the other group.
  const check = useCheckItem({ onSuccess: (saved) => onToggled?.(saved, true), onError });
  const uncheck = useUncheckItem({ onSuccess: (saved) => onToggled?.(saved, false), onError });
  const remove = useDeleteItem();
  const [more, setMore] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const name = itemName(item);
  const checked = item.checked_at !== null;
  const amounts = formatAmounts(item.amounts);
  const pack = item.product
    ? formatPack(
        item.product.pack_qty,
        item.product.pack_unit,
        item.product.pack_count,
        item.product.piece_name,
      )
    : "";
  const toggling = check.isPending || uncheck.isPending;

  const toggle = () => {
    const vars = { id: item.id, listKey };
    if (checked) uncheck.mutate(vars);
    else check.mutate(vars);
  };

  const doRemove = () => {
    setConfirmRemove(false);
    remove.mutate(item.id, {
      onSuccess: () => onNotice(`Removed ${name} from the list.`),
      onError,
    });
  };
  const claimedByOther = item.claimed_by !== null && item.claimed_by.id !== me.data?.id;

  return (
    <li
      data-testid="list-item"
      data-checked={checked ? "true" : "false"}
      className="border-t border-neutral-200 py-2 first:border-t-0 dark:border-neutral-800"
    >
      <div className="flex items-start gap-2">
        <button
          type="button"
          role="checkbox"
          aria-checked={checked}
          aria-label={checked ? `Uncheck ${name}` : `Check ${name}`}
          disabled={readOnly || toggling}
          onClick={toggle}
          className={`inline-flex size-11 shrink-0 items-center justify-center rounded-md lg:size-8 ${focusRing} disabled:cursor-not-allowed`}
        >
          <span
            aria-hidden="true"
            className={`inline-flex size-7 items-center justify-center rounded-full border-2 ${
              checked
                ? "border-green-700 bg-green-700 text-white dark:border-green-500 dark:bg-green-500 dark:text-neutral-950"
                : "border-neutral-400 bg-white dark:border-neutral-500 dark:bg-neutral-900"
            }`}
          >
            {checked ? (
              <svg
                viewBox="0 0 16 16"
                className="size-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M3 8.5l3 3 7-7" />
              </svg>
            ) : null}
          </span>
        </button>

        <div className="min-w-0 flex-1 pt-2.5 lg:pt-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span
              className={`font-medium ${checked ? `line-through ${muted}` : ""}`}
              data-testid="item-name"
            >
              {name}
            </span>
            {pack ? <span className={`text-xs ${muted}`}>{pack}</span> : null}
            {amounts ? (
              <span className="text-sm tabular-nums" data-testid="item-amounts">
                {amounts}
              </span>
            ) : null}
            {item.deferrable ? <Badge>deferrable</Badge> : null}
            {item.min_quality !== null ? (
              <span className="text-xs">
                <QualityStars rating={item.min_quality} />
              </span>
            ) : null}
            {item.added_from === "recipe" && item.recipe_id ? (
              <Link
                to={`/cook/recipes/${item.recipe_id}`}
                className={`inline-flex min-h-11 items-center rounded text-xs underline lg:min-h-0 ${muted} ${focusRing}`}
              >
                from recipe
              </Link>
            ) : null}
          </div>
          {item.note ? <p className={`text-sm ${muted}`}>{item.note}</p> : null}
          {checked && item.checked_by ? (
            <p className={`text-xs ${muted}`}>
              Checked by {item.checked_by.display_name} ·{" "}
              <time dateTime={item.checked_at ?? undefined}>{formatDateTime(item.checked_at)}</time>
            </p>
          ) : null}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
            <ClaimChip item={item} readOnly={readOnly} onError={onError} />
            {readOnly ? null : (
              <Button
                variant="ghost"
                className="min-h-11 px-2 text-xs lg:min-h-8"
                aria-haspopup="dialog"
                aria-label={`More actions for ${name}`}
                onClick={() => setMore(true)}
              >
                More actions
              </Button>
            )}
          </div>
          {confirmRemove ? (
            <Alert tone="warn" className="mt-2">
              <span className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  Remove {name}? {item.claimed_by?.display_name} is getting it.
                </span>
                <span className="flex gap-2">
                  <Button variant="secondary" onClick={() => setConfirmRemove(false)} autoFocus>
                    Cancel
                  </Button>
                  <Button variant="dangerFill" onClick={doRemove}>
                    Remove
                  </Button>
                </span>
              </span>
            </Alert>
          ) : null}
          {remove.isError ? (
            <Alert tone="error" className="mt-2">
              Couldn&apos;t remove {name}: {errorMessage(remove.error)}
            </Alert>
          ) : null}
        </div>
      </div>

      {more ? (
        <MoreActionsDialog
          actions={[
            {
              label: "Edit",
              open: () => {
                setMore(false);
                setEditing(true);
              },
            },
            {
              label: remove.isPending ? "Removing…" : "Remove",
              variant: "danger",
              disabled: remove.isPending,
              open: () => {
                setMore(false);
                if (claimedByOther) setConfirmRemove(true);
                else doRemove();
              },
            },
          ]}
          onClose={() => setMore(false)}
        />
      ) : null}
      {editing ? (
        <EditItemDrawer
          item={item}
          listKey={listKey}
          onClose={() => setEditing(false)}
          onNotice={onNotice}
        />
      ) : null}
    </li>
  );
}
