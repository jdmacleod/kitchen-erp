import { useEffect, useRef, useState } from "react";
import { catalogErrorMessage } from "../../api/catalog";
import { useForgetProductCode, useProductCodes, type ProductCode } from "../../api/productCodes";
import { Alert, Button, Card, sectionHeading } from "../ui";

const SCHEME_LABELS: Record<ProductCode["scheme"], string> = {
  gtin: "Barcode",
  other: "Barcode",
  plu: "PLU",
  vendor_sku: "Item number",
  rw_item: "Weighed-item code",
};

/**
 * What a code is and where it reads: "Weighed-item code at Juniper Market". Mid-sentence
 * the kind is lower case, and the store keeps its own capitals.
 */
export function codeLabel(code: ProductCode, midSentence = false): string {
  const label = SCHEME_LABELS[code.scheme];
  const kind = midSentence ? label.toLowerCase() : label;
  return code.vendor_name ? `${kind} at ${code.vendor_name}` : kind;
}

/**
 * The codes a product is known by. A store code remembered from a receipt can be
 * forgotten here, since a code that names a counter rather than the item would
 * keep identifying the wrong lines (issue 448). The barcode is changed in the form.
 */
export function ProductCodesCard({ productId }: { productId: string }) {
  const codes = useProductCodes(productId);
  const forget = useForgetProductCode(productId);
  const [asking, setAsking] = useState<ProductCode | null>(null);
  const [forgot, setForgot] = useState<string | null>(null);

  if (codes.isError) return <Alert tone="error">{catalogErrorMessage(codes.error)}</Alert>;
  if (!codes.data || codes.data.length === 0) return null;

  return (
    <Card>
      <h2 className={`${sectionHeading} mb-1`}>Codes</h2>
      <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
        Receipt lines and scans that print one of these are identified as this product.
      </p>
      <ul className="flex flex-col divide-y divide-neutral-200 dark:divide-neutral-700">
        {codes.data.map((code) => (
          <li key={code.id} className="flex flex-col gap-2 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span>
                <span className="font-medium tabular-nums">{code.value}</span>
                <span className="text-neutral-600 dark:text-neutral-400">
                  {" "}
                  · {codeLabel(code)}
                  {code.source === "receipt" ? " · remembered from a receipt" : ""}
                </span>
              </span>
              {code.is_barcode ? (
                <span className="text-xs text-neutral-600 dark:text-neutral-400">
                  Changed in Edit product
                </span>
              ) : asking?.id === code.id ? null : (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setForgot(null);
                    forget.reset();
                    setAsking(code);
                  }}
                >
                  Forget
                </Button>
              )}
            </div>
            {asking?.id === code.id ? (
              <ForgetConfirm
                code={code}
                busy={forget.isPending}
                onCancel={() => setAsking(null)}
                onForget={() =>
                  forget.mutate(code.id, {
                    onSuccess: () => {
                      setAsking(null);
                      setForgot(`Forgot ${code.value}.`);
                    },
                  })
                }
              />
            ) : null}
          </li>
        ))}
      </ul>
      {forgot ? (
        <p role="status" className="mt-2 text-sm">
          {forgot}
        </p>
      ) : null}
      {forget.error ? (
        <Alert tone="error" className="mt-2">
          {catalogErrorMessage(forget.error)}
        </Alert>
      ) : null}
    </Card>
  );
}

function ForgetConfirm({
  code,
  busy,
  onCancel,
  onForget,
}: {
  code: ProductCode;
  busy: boolean;
  onCancel: () => void;
  onForget: () => void;
}) {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => cancel.current?.focus(), []);
  return (
    <div
      role="group"
      aria-label={`Forget ${code.value}`}
      className="flex flex-col gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm dark:border-red-900 dark:bg-red-950"
    >
      <p className="font-medium">
        Forget {code.value} ({codeLabel(code, true)})?
      </p>
      <p>
        Lines it already identified keep this product. New lines that print it wait to be
        identified.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="danger" disabled={busy} onClick={onForget}>
          {busy ? "Forgetting…" : "Forget"}
        </Button>
        <Button ref={cancel} variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
