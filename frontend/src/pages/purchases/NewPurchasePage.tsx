import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useCreatePurchase } from "../../api/purchases";
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

  return (
    <>
      <PageHeader title="New purchase">
        <Link to="/prices/new" className={`rounded text-sm underline ${focusRing}`}>
          Just noting a shelf price?
        </Link>
      </PageHeader>
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
                navigate(`/purchases/${purchase.id}`);
              },
            })
          }
        />
      </Card>
    </>
  );
}
