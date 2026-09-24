import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { formatPack, isPositiveDecimal, productTitle } from "../../api/catalog";
import { purchaseErrorMessage, useCreateObservation, type Observation, type ObservationCreateInput } from "../../api/purchases";
import { UnitSelect } from "../../components/catalog/UnitSelect";
import { LocationGuard } from "../../components/purchases/LocationGuard";
import { LocationSelect, rememberLocation } from "../../components/purchases/LocationSelect";
import { ProductPicker, type ProductRef } from "../../components/purchases/ProductPicker";
import { Alert, Button, Card, Field, PageHeader, focusRing } from "../../components/ui";
import { formatMoney, isNonNegativeDecimal, stripZeros } from "../../lib/decimal";
import { fromDateTimeLocal, toDateTimeLocal } from "../../lib/openingHours";
import { usePageTitle } from "../../lib/usePageTitle";

/**
 * Record a posted price without buying anything, in one screen. Enter in any
 * field saves; after a save the product box is focused for the next tag.
 */
export function ShelfPricePage() {
  usePageTitle("Shelf price");
  const create = useCreateObservation();
  const [locationId, setLocationId] = useState("");
  const [product, setProduct] = useState<ProductRef | null>(null);
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState("1");
  const [unit, setUnit] = useState("");
  const [promo, setPromo] = useState(false);
  const [observedAt, setObservedAt] = useState(() => toDateTimeLocal(new Date()));
  const [observedTouched, setObservedTouched] = useState(false);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [result, setResult] = useState<Observation | null>(null);

  const productRef = useRef<HTMLInputElement>(null);
  const focusProductNext = useRef(false);
  useEffect(() => {
    if (!focusProductNext.current) return;
    focusProductNext.current = false;
    productRef.current?.focus();
  });

  const onPicked = () => {
    // One "each" is one pack; for loose goods the person types the weighed unit.
    setUnit("each");
    document.getElementById("shelf-price")?.focus();
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (create.isPending) return;
    setResult(null);
    if (!locationId) return setInvalid("Choose a location.");
    if (!product) return setInvalid("Choose a product, or create one.");
    if (price.trim() === "" || !isNonNegativeDecimal(price)) return setInvalid("Enter the posted price.");
    if (!isPositiveDecimal(qty)) return setInvalid("Quantity must be a positive number.");
    if (!unit) return setInvalid("Choose a unit.");
    const at = observedTouched ? fromDateTimeLocal(observedAt) : undefined;
    if (observedTouched && !at) return setInvalid("Enter a valid observation time.");
    setInvalid(null);

    const input: ObservationCreateInput = {
      product_id: product.id,
      vendor_location_id: locationId,
      price: price.trim(),
      qty: qty.trim(),
      unit,
    };
    if (promo) input.is_promo = true;
    if (at) input.observed_at = at;

    create.mutate(input, {
      onSuccess: (observation) => {
        rememberLocation(locationId);
        setResult(observation);
        setProduct(null);
        setPrice("");
        setQty("1");
        setUnit("");
        setPromo(false);
        if (!observedTouched) setObservedAt(toDateTimeLocal(new Date()));
        focusProductNext.current = true;
      },
    });
  };

  // Enter in the product box picks a highlighted hit; a bare Enter there must
  // not save a form that has no product yet.
  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key !== "Enter" || event.isDefaultPrevented()) return;
    const target = event.target as HTMLElement;
    if (target.getAttribute("role") === "combobox") event.preventDefault();
  };

  const busy = create.isPending;
  const pack = product ? formatPack(product.pack_qty, product.pack_unit) : "";

  return (
    <>
      <PageHeader title="Shelf price">
        <Link to="/purchases/new" className={`rounded text-sm underline ${focusRing}`}>
          Enter a purchase instead
        </Link>
      </PageHeader>
      <LocationGuard>
        <Card>
          <form onSubmit={submit} onKeyDown={onKeyDown} aria-labelledby="shelf-heading" noValidate className="flex flex-col gap-4">
            <h2 id="shelf-heading" className="text-lg font-medium">
              Record a posted price
            </h2>
            {result ? <ObservationResult observation={result} /> : null}
            {invalid ? <Alert tone="error">{invalid}</Alert> : null}
            {create.error ? <Alert tone="error">{purchaseErrorMessage(create.error)}</Alert> : null}

            <LocationSelect id="shelf-location" value={locationId} onChange={setLocationId} disabled={busy} />

            <ProductPicker
              id="shelf-product"
              value={product}
              onChange={(p) => {
                setProduct(p);
                if (!p) setUnit("");
              }}
              onPicked={onPicked}
              inputRef={productRef}
              disabled={busy}
              hint="Arrow keys move, Enter picks. New product creates the product and, if needed, its ingredient."
            />

            <div className="grid gap-4 sm:grid-cols-3">
              <Field
                id="shelf-price"
                label="Price"
                inputMode="decimal"
                autoComplete="off"
                required
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                disabled={busy}
                hint="What the tag says, before tax."
              />
              <Field
                id="shelf-qty"
                label="Quantity"
                inputMode="decimal"
                autoComplete="off"
                required
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                disabled={busy}
              />
              <UnitSelect
                id="shelf-unit"
                label="Unit"
                value={unit}
                onChange={setUnit}
                disabled={busy}
                hint={pack ? `1 each = one ${pack} pack.` : undefined}
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="inline-flex min-h-10 items-center gap-2 text-sm">
                <input type="checkbox" checked={promo} onChange={(e) => setPromo(e.target.checked)} disabled={busy} className={`size-4 ${focusRing}`} />
                Sale price
              </label>
              <Field
                id="shelf-observed-at"
                label="Observed at"
                type="datetime-local"
                value={observedAt}
                onChange={(e) => {
                  setObservedAt(e.target.value);
                  setObservedTouched(true);
                }}
                disabled={busy}
                hint={observedTouched ? undefined : "Now, unless you change it."}
              />
            </div>

            <div>
              <Button type="submit" disabled={busy}>
                {busy ? "Saving…" : "Save price"}
              </Button>
            </div>
          </form>
        </Card>
      </LocationGuard>
    </>
  );
}

/** What normalization made of the observation, or what it is missing and where to fix it. */
export function ObservationResult({ observation }: { observation: Observation }) {
  const { product, norm } = observation;
  const recorded = (
    <>
      Recorded {formatMoney(observation.price)} for {stripZeros(observation.qty)} {observation.unit} of{" "}
      <Link to={`/products/${product.id}`} className={`rounded font-medium underline ${focusRing}`}>
        {productTitle(product)}
      </Link>
      {observation.is_promo ? " (sale)" : ""}.
    </>
  );

  if (!norm) {
    return (
      <Alert tone="success">
        {recorded} Normalization is pending.
      </Alert>
    );
  }

  if (norm.status === "ok") {
    const bridge = norm.bridge_kind && norm.bridge_kind !== "none" ? ` via ${norm.bridge_kind}${norm.bridge_source ? ` (${norm.bridge_source}${norm.bridge_confirmed === false ? ", unconfirmed" : ""})` : ""}` : "";
    return (
      <Alert tone="success">
        {recorded}{" "}
        <span data-testid="norm-price" className="font-medium">
          {formatMoney(norm.norm_unit_price, 2, 6)} per {norm.norm_unit}
        </span>
        {bridge}.
      </Alert>
    );
  }

  const ingredientLink = (hash: string, text: string) => (
    <Link to={`/ingredients/${product.ingredient.id}#${hash}`} className={`rounded font-medium underline ${focusRing}`}>
      {text}
    </Link>
  );
  const productLink = (text: string) => (
    <Link to={`/products/${product.id}`} className={`rounded font-medium underline ${focusRing}`}>
      {text}
    </Link>
  );

  let missing: ReactNode;
  switch (norm.status) {
    case "no_density":
      missing = (
        <>
          {product.ingredient.name} has no density, so {observation.unit} cannot be turned into {product.ingredient.canonical_unit}.{" "}
          {ingredientLink("density-heading", "Add a density")}.
        </>
      );
      break;
    case "unknown_measure":
      missing = (
        <>
          {product.ingredient.name} has no measure named “{observation.unit}”. {ingredientLink("measures-heading", "Add the measure")}.
        </>
      );
      break;
    case "no_pack":
      missing = <>{productTitle(product)} has no pack size, so “each” cannot be priced per {product.ingredient.canonical_unit}. {productLink("Set the pack")}.</>;
      break;
    default:
      missing = <>The quantity could not be used. {productLink("Check the product")}.</>;
  }

  return (
    <Alert tone="info">
      {recorded} Not yet priced per {product.ingredient.canonical_unit}: {missing}
    </Alert>
  );
}
