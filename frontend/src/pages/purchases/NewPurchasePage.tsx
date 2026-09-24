import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useCreatePurchase } from "../../api/purchases";
import { LocationGuard } from "../../components/purchases/LocationGuard";
import { rememberLocation } from "../../components/purchases/LocationSelect";
import { PurchaseForm, emptyPurchaseValues } from "../../components/purchases/PurchaseForm";
import { Card, PageHeader, focusRing } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

/** Manual entry for a purchase with no receipt. Saving commits it at once. */
export function NewPurchasePage() {
  usePageTitle("New purchase");
  const navigate = useNavigate();
  const create = useCreatePurchase();
  const [values, setValues] = useState(emptyPurchaseValues);
  // Set by the first-run checklist's step two, and only there: that link exists
  // only while the household has no committed purchase, so the flag is true by
  // construction rather than by asking. Read once at mount, because this page
  // unmounts on success and the message has to be carried to where the user lands.
  // Someone who opens this form from the sidebar instead gets no acknowledgement;
  // that is the accepted cost of not putting a query on the weekly hot path.
  const routerState = useLocation().state as { firstPurchase?: boolean } | null;
  const [firstPurchase] = useState(() => routerState?.firstPurchase === true);

  return (
    <>
      <PageHeader title="New purchase">
        <Link to="/prices/new" className={`rounded text-sm underline ${focusRing}`}>
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
                  navigate(`/purchases/${purchase.id}`, firstPurchase ? { state: { firstPurchase: true } } : undefined);
                },
              })
            }
          />
        </Card>
      </LocationGuard>
    </>
  );
}
