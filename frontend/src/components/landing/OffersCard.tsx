import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { useCurrentOffers, type CurrentOffer } from "../../api/flyerOffers";
import { formatCalendarDate } from "../../lib/format";
import { useCurrentKitchen } from "../../lib/kitchen";
import { distinctOffers, offerPriceText } from "../../lib/offers";
import { Alert, focusRing, sectionHeading, tapTarget } from "../ui";

/**
 * Home's "This week's offers" (18, 2X; UI-9.1): the current kitchen's linked offers,
 * grouped by store, each linking to its product. Hidden while there are none, and
 * while it is still asking; an error shows as an error.
 */
export function OffersCard() {
  const { current } = useCurrentKitchen();
  const offers = useCurrentOffers(current?.id);

  if (offers.isError) {
    return (
      <section aria-labelledby="this-weeks-offers">
        <h2 id="this-weeks-offers" className={sectionHeading}>
          This week&apos;s offers
        </h2>
        <Alert tone="error" className="mt-2">
          {errorMessage(offers.error)}
        </Alert>
      </section>
    );
  }
  if (!offers.data?.length) return null;

  const stores = new Map<string, { name: string; offers: CurrentOffer[] }>();
  for (const offer of distinctOffers(offers.data)) {
    const store = stores.get(offer.vendor.id) ?? { name: offer.vendor.name, offers: [] };
    store.offers.push(offer);
    stores.set(offer.vendor.id, store);
  }

  return (
    <section aria-labelledby="this-weeks-offers">
      <h2 id="this-weeks-offers" className={sectionHeading}>
        This week&apos;s offers
      </h2>
      {[...stores.entries()].map(([id, store]) => (
        <section key={id} aria-label={store.name} className="mt-3">
          <h3 className="text-sm font-medium">{store.name}</h3>
          <ul className="mt-1">
            {store.offers.map((offer) => (
              <li
                key={offer.id}
                className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-t border-neutral-200 py-1.5 text-sm first:border-t-0 dark:border-neutral-800"
              >
                <Link
                  to={`/catalog/products/${offer.product.id}`}
                  className={`${tapTarget} min-w-0 flex-1 rounded-sm underline-offset-2 hover:underline ${focusRing}`}
                >
                  {offer.product.name}
                </Link>
                <span className="tabular-nums">{offerPriceText(offer)}</span>
                <span className="text-xs text-neutral-600 dark:text-neutral-400">
                  through {formatCalendarDate(offer.valid_to)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </section>
  );
}
