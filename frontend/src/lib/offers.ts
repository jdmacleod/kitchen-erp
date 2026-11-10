import type { CurrentOffer, QueuedOffer } from "../api/flyerOffers";
import type { ShoppingItem } from "../api/shoppingList";
import { formatMoney, stripZeros } from "./decimal";

/**
 * A flyer offer's price as the circular prints it (18; UI-9.1): "3 for $5",
 * "$2.49/lb", "Buy one get one", "20% off", "$1 off", with "card price" when it
 * needs a loyalty card. Whole dollars drop their cents, as circulars print them.
 */
export function offerPriceText(
  offer: Pick<QueuedOffer, "price_basis" | "price" | "basis_qty" | "discount" | "requires_loyalty">,
): string {
  const money = (a: string | null) => {
    const text = formatMoney(a);
    return text.endsWith(".00") ? text.slice(0, -3) : text;
  };
  const base = (() => {
    switch (offer.price_basis) {
      case "each":
        return money(offer.price);
      case "per_lb":
        return `${money(offer.price)}/lb`;
      case "per_oz":
        return `${money(offer.price)}/oz`;
      case "multi":
        return `${stripZeros(offer.basis_qty ?? "1")} for ${money(offer.price)}`;
      case "bogo":
        return offer.price ? `Buy one get one, ${money(offer.price)}` : "Buy one get one";
      case "percent_off":
        return `${stripZeros(offer.discount ?? "0")}% off`;
      case "dollar_off":
        return `${money(offer.discount)} off`;
    }
  })();
  return offer.requires_loyalty ? `${base}, card price` : base;
}

type DealOffer = Pick<
  CurrentOffer,
  | "id"
  | "vendor"
  | "location"
  | "product"
  | "price_basis"
  | "price"
  | "basis_qty"
  | "discount"
  | "requires_loyalty"
  | "valid_to"
>;

/** One deal and where it runs: `locations` is null when every branch of the store runs it. */
export interface Deal<T extends DealOffer> {
  offer: T;
  locations: string[] | null;
}

function terms(o: DealOffer): string {
  return [o.price_basis, o.price, o.basis_qty, o.discount, o.requires_loyalty, o.valid_to].join(
    "|",
  );
}

/**
 * Offers grouped by `group` (a store and product on Home, a store for a list item),
 * one deal per distinct set of terms: the same circular at several branches is one
 * line, and branches whose deals differ are named (UI-9.1, UI-9.2).
 */
export function distinctDeals<T extends DealOffer>(
  offers: readonly T[],
  group: (o: T) => string,
): Deal<T>[] {
  const groups = new Map<string, Map<string, Deal<T> & { names: string[] }>>();
  for (const o of offers) {
    const deals = groups.get(group(o)) ?? new Map<string, Deal<T> & { names: string[] }>();
    const deal = deals.get(terms(o)) ?? { offer: o, locations: null, names: [] };
    deal.names.push(o.location.name);
    deals.set(terms(o), deal);
    groups.set(group(o), deals);
  }
  return [...groups.values()].flatMap((deals) =>
    [...deals.values()].map(({ offer, names }) => ({
      offer,
      locations: deals.size > 1 ? names : null,
    })),
  );
}

/** The current offers for a list item, by store: its product's, or any of its ingredient's (UI-9.2). */
export function offersForItem(
  item: Pick<ShoppingItem, "kind" | "ingredient" | "product">,
  offers: readonly CurrentOffer[],
): Deal<CurrentOffer>[] {
  const matches = offers.filter((o) =>
    item.kind === "product"
      ? o.product.id === item.product?.id
      : item.ingredient !== null && o.product.ingredient_id === item.ingredient.id,
  );
  return distinctDeals(matches, (o) => `${o.vendor.id}:${o.product.id}`);
}
