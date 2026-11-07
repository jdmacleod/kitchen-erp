import type { QueuedOffer } from "../api/flyerOffers";
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
