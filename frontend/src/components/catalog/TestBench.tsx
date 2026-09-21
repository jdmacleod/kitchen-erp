import { useState, type FormEvent } from "react";
import {
  catalogErrorMessage,
  productTitle,
  trimDecimal,
  useConvert,
  useProducts,
  type ConvertResult,
  type Ingredient,
  type Provenance,
} from "../../api/catalog";
import { Alert, Button, Card, Field } from "../ui";
import { Badge, ConfirmedBadge, SelectField } from "./fields";

const bridgeLabel: Record<Provenance["bridge_kind"], string> = {
  none: "no bridge (same dimension)",
  density: "ingredient density",
  density_override: "product density override",
  measure: "named measure",
  pack: "product pack",
};

/**
 * Enter a quantity and a unit (or a measure label), optionally through a
 * product, and see exactly what the conversion library returns: the canonical
 * result with its provenance, or the typed failure.
 */
export function TestBench({ ingredient }: { ingredient: Ingredient }) {
  const convert = useConvert(ingredient.id);
  const products = useProducts({ ingredientId: ingredient.id });
  const [qty, setQty] = useState("1");
  const [unit, setUnit] = useState("");
  const [productId, setProductId] = useState("");
  const productItems = products.data?.pages.flatMap((p) => p.items) ?? [];

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    convert.mutate({
      qty: qty.trim() === "" ? null : qty.trim(),
      unit: unit.trim(),
      ...(productId ? { product_id: productId } : {}),
    });
  };

  return (
    <Card>
      <h2 className="mb-1 text-lg font-medium">Test bench</h2>
      <p className="mb-4 text-sm text-neutral-600 dark:text-neutral-400">
        Runs the same conversion Phase 2 will run on a receipt line. Leave the quantity blank to see the
        “no quantity” failure.
      </p>
      <form onSubmit={onSubmit} className="flex flex-col gap-3" aria-label="Conversion test bench">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field
            id="bench-qty"
            label="Quantity"
            inputMode="decimal"
            autoComplete="off"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
          <Field
            id="bench-unit"
            label="Unit or measure"
            placeholder="cup, lbs, Tbsp, clove…"
            autoComplete="off"
            required
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
          />
          <SelectField
            id="bench-product"
            label="Through product"
            value={productId}
            onChange={(e) => setProductId(e.target.value)}
            hint="Optional. Brings in the product's pack and density override."
          >
            <option value="">None</option>
            {productItems.map((p) => (
              <option key={p.id} value={p.id}>
                {productTitle(p)}
              </option>
            ))}
          </SelectField>
        </div>
        <div>
          <Button type="submit" disabled={convert.isPending || unit.trim() === ""}>
            {convert.isPending ? "Converting…" : "Convert"}
          </Button>
        </div>
      </form>
      {convert.isError ? (
        <Alert tone="error" className="mt-4">
          {catalogErrorMessage(convert.error)}
        </Alert>
      ) : null}
      {convert.data ? <BenchResult result={convert.data} /> : null}
    </Card>
  );
}

function BenchResult({ result }: { result: ConvertResult }) {
  if (!result.ok) {
    return (
      <div role="status" className="mt-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950/40">
        <p className="font-medium">
          Failed: <code data-testid="bench-failure-code">{result.failure_code ?? "unknown"}</code>
        </p>
        {result.message ? <p className="mt-1">{result.message}</p> : null}
        <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">convert {result.version}</p>
      </div>
    );
  }
  const p = result.provenance;
  return (
    <div role="status" className="mt-4 rounded-md border border-green-300 bg-green-50 p-3 text-sm dark:border-green-900 dark:bg-green-950/40">
      <p className="text-base font-medium" data-testid="bench-result">
        = {trimDecimal(result.qty ?? "")} {result.unit}
      </p>
      {p ? <ProvenanceView provenance={p} /> : null}
      <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">convert {result.version}</p>
    </div>
  );
}

function ProvenanceView({ provenance, nested = false }: { provenance: Provenance; nested?: boolean }) {
  return (
    <dl className={`mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 ${nested ? "border-l-2 border-neutral-300 pl-3 dark:border-neutral-700" : ""}`}>
      <dt className="text-neutral-600 dark:text-neutral-400">Bridge</dt>
      <dd>{bridgeLabel[provenance.bridge_kind] ?? provenance.bridge_kind}</dd>
      {provenance.source ? (
        <>
          <dt className="text-neutral-600 dark:text-neutral-400">Source</dt>
          <dd>{provenance.source}</dd>
        </>
      ) : null}
      {provenance.confirmed !== null && provenance.confirmed !== undefined ? (
        <>
          <dt className="text-neutral-600 dark:text-neutral-400">State</dt>
          <dd>
            <ConfirmedBadge confirmed={provenance.confirmed} />
          </dd>
        </>
      ) : null}
      {provenance.detail ? (
        <>
          <dt className="text-neutral-600 dark:text-neutral-400">Detail</dt>
          <dd>{provenance.detail}</dd>
        </>
      ) : null}
      {!nested ? (
        <>
          <dt className="text-neutral-600 dark:text-neutral-400">Trust</dt>
          <dd>
            {provenance.rests_on_unconfirmed ? (
              <Badge tone="warn">rests on an unconfirmed bridge</Badge>
            ) : (
              <Badge tone="good">no unconfirmed bridges</Badge>
            )}
          </dd>
        </>
      ) : null}
      {provenance.via ? (
        <>
          <dt className="text-neutral-600 dark:text-neutral-400">Via</dt>
          <dd>
            <ProvenanceView provenance={provenance.via} nested />
          </dd>
        </>
      ) : null}
    </dl>
  );
}
