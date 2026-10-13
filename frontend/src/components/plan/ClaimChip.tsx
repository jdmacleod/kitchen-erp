import { useMe } from "../../api/queries";
import { useClaimItem, useUnclaimItem, type ShoppingItem } from "../../api/shoppingList";
import { Badge } from "../catalog/fields";
import { Button } from "../ui";

/**
 * The claim on a row (10, Plan: Claim chip; UI-8.6): a neutral "{name} is
 * getting it" pill once claimed, "I'll get it" as a quiet action on an unclaimed
 * row, and "Unclaim" for the claimant. A claim is only a display: anyone may
 * still check the item, and the check clears it (L2).
 */
export function ClaimChip({
  item,
  readOnly = false,
  onError,
}: {
  item: ShoppingItem;
  readOnly?: boolean;
  onError?: (e: unknown) => void;
}) {
  const me = useMe();
  const claim = useClaimItem();
  const unclaim = useUnclaimItem();
  const busy = claim.isPending || unclaim.isPending;
  const quiet = "min-h-11 px-2 text-xs lg:min-h-8";

  if (item.checked_at) return null;
  if (item.claimed_by) {
    const mine = me.data?.id === item.claimed_by.id;
    return (
      <span className="inline-flex flex-wrap items-center gap-1">
        <Badge>
          {mine ? "You're getting it" : `${item.claimed_by.display_name} is getting it`}
        </Badge>
        {mine && !readOnly ? (
          <Button
            variant="ghost"
            className={quiet}
            disabled={busy}
            onClick={() => unclaim.mutate(item.id, { onError })}
          >
            {unclaim.isPending ? "Unclaiming…" : "Unclaim"}
          </Button>
        ) : null}
      </span>
    );
  }
  if (readOnly) return null;
  return (
    <Button
      variant="ghost"
      className={quiet}
      disabled={busy}
      onClick={() => claim.mutate(item.id, { onError })}
    >
      {claim.isPending ? "Claiming…" : "I'll get it"}
    </Button>
  );
}
