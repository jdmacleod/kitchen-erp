import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { itemName, useShoppingList } from "../../api/shoppingList";
import { useNotice } from "../Notice";
import { AddItemRow } from "../plan/AddItemRow";
import { formatAmounts } from "../plan/amounts";
import { ClaimChip } from "../plan/ClaimChip";
import { KitchenTag } from "../plan/KitchenTag";
import { Alert, focusRing, tapTarget } from "../ui";
import { useCurrentKitchen } from "../../lib/kitchen";

const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * Home's "{Kitchen} list · {n} to get" panel (10, Home list panel; UI-8.9): the
 * five oldest unchecked items with their amounts and claim chips, a one-field
 * add, and "Open list". Desktop only (L5): the phone Home keeps its recent
 * purchases, since the List tab is one tap away. Rendered only with `plan`.
 */
export function ShoppingListPanel() {
  const kitchen = useCurrentKitchen();
  const list = useShoppingList(kitchen.currentId);
  const notice = useNotice();
  if (!kitchen.ready || !kitchen.currentId) return null;
  const name = list.data?.home_base_name ?? kitchen.current?.name ?? "Kitchen";
  const unchecked = list.data?.unchecked ?? [];
  const count = unchecked.length;

  return (
    <section
      aria-labelledby="shopping-list-panel"
      className="hidden lg:block"
      data-testid="shopping-list-panel"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h2
          id="shopping-list-panel"
          className="flex flex-wrap items-center gap-x-3 gap-y-1 font-display text-lg"
        >
          <span>
            {name} list{list.data ? ` · ${count} to get` : ""}
          </span>
          <KitchenTag name={name} />
        </h2>
        <Link
          to="/plan/list"
          className={`${tapTarget} shrink-0 whitespace-nowrap rounded text-sm underline underline-offset-2 hover:no-underline ${focusRing}`}
        >
          Open list
        </Link>
      </div>
      {list.isError ? (
        <Alert tone="error" className="mt-2">
          Couldn&apos;t load the list: {errorMessage(list.error)}
        </Alert>
      ) : list.isPending ? (
        <p role="status" className={`mt-2 text-sm ${muted}`}>
          Loading…
        </p>
      ) : count === 0 ? (
        <p className={`mt-2 text-sm ${muted}`}>Nothing on the list.</p>
      ) : (
        <ul className="mt-2">
          {unchecked.slice(0, 5).map((item) => {
            const amounts = formatAmounts(item.amounts);
            return (
              <li
                key={item.id}
                className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-neutral-200 py-2 text-sm first:border-t-0 dark:border-neutral-800"
                data-testid="panel-item"
              >
                <span className="min-w-0 flex-1 font-medium">{itemName(item)}</span>
                {amounts ? <span className="tabular-nums">{amounts}</span> : null}
                <ClaimChip item={item} readOnly />
              </li>
            );
          })}
          {count > 5 ? <li className={`pt-2 text-xs ${muted}`}>and {count - 5} more</li> : null}
        </ul>
      )}
      <div className="mt-3">
        <AddItemRow
          homeBaseId={kitchen.currentId}
          compact
          onAdded={(item) =>
            notice.show({
              tone: "success",
              message: `Added ${itemName(item)} to the ${name} list.`,
              action: { label: "Open list", to: "/plan/list" },
            })
          }
        />
      </div>
    </section>
  );
}
