import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useCreatePurchase } from "../../api/purchases";
import { LocationGuard } from "../../components/purchases/LocationGuard";
import { rememberLocation } from "../../components/purchases/LocationSelect";
import { PurchaseForm, emptyPurchaseValues } from "../../components/purchases/PurchaseForm";
import { Card, PageHeader, focusRing } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";
import { useNavigateWithNotice, type NoticeData } from "../../components/Notice";

// The first-run arc closes where the work finished, rather than on a later visit
// to Home that may be days away. The Notice carries it to the purchase page and
// is consumed there, so Back and reload do not congratulate the same purchase.
export const FIRST_PURCHASE_NOTICE: NoticeData = {
  tone: "success",
  message: "That is your kitchen set up. This purchase is in the price book, and the next one will have something to compare against.",
};

/** Manual entry for a purchase with no receipt. Saving commits it at once. */
export function NewPurchasePage() {
  usePageTitle("New purchase");
  const navigate = useNavigate();
  const navigateWithNotice = useNavigateWithNotice();
  const create = useCreatePurchase();
  const routerLocation = useLocation();
  const routerState = routerLocation.state as { firstPurchase?: boolean; locationId?: string } | null;
  // A store chosen in Capture comes along; the location select keeps it (G2).
  const [values, setValues] = useState(() => ({ ...emptyPurchaseValues(), vendor_location_id: routerState?.locationId ?? "" }));
  // Set by the first-run checklist's step two, and only there: that link exists
  // only while the household has no committed purchase, so the flag is true by
  // construction rather than by asking. Read once at mount, because this page
  // unmounts on success and the message has to be carried to where the user lands.
  // Someone who opens this form from the sidebar instead gets no acknowledgement;
  // that is the accepted cost of not putting a query on the weekly hot path.
  const [firstPurchase] = useState(() => routerState?.firstPurchase === true);
  // Strip it from this entry as well as the destination's. Saving pushes a new
  // entry for the purchase, so pressing Back lands here again — and without this
  // the form would still be holding the flag and would congratulate the next
  // purchase too. The value is already captured above, so clearing is safe.
  useEffect(() => {
    if (routerState?.firstPurchase !== true) return;
    navigate(routerLocation.pathname, { replace: true, state: null });
  }, [routerState, routerLocation.pathname, navigate]);

  return (
    <>
      <PageHeader title="New purchase" description="A market or stand without a receipt, entered line by line.">
        <Link to="/shop/shelf-prices" className={`rounded text-sm underline ${focusRing}`}>
          Just noting a shelf price?
        </Link>
      </PageHeader>
      <LocationGuard>
        <Card>
          <PurchaseForm
            idPrefix="new-purchase"
            heading="Enter a purchase"
            values={values}
            onChange={setValues}
            busy={create.isPending}
            error={create.error}
            submitLabel="Save purchase"
            busyLabel="Saving…"
            onSubmit={(input) =>
              create.mutate(input, {
                onSuccess: (purchase) => {
                  rememberLocation(input.vendor_location_id);
                  const to = `/shop/purchases/${purchase.id}`;
                  if (firstPurchase) navigateWithNotice(to, FIRST_PURCHASE_NOTICE);
                  else navigate(to);
                },
              })
            }
          />
        </Card>
      </LocationGuard>
    </>
  );
}
