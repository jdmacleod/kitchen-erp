import { useState, type KeyboardEvent, type RefObject } from "react";
import {
  catalogErrorMessage,
  formatPack,
  isPositiveDecimal,
  productTitle,
  useCreateProduct,
  type CanonicalUnit,
  type Product,
  type ProductCreateInput,
  type SearchHit,
} from "../../api/catalog";
import { Badge, labelClass } from "../catalog/fields";
import { IngredientPicker, type IngredientChoice } from "../catalog/IngredientPicker";
import { ProductTypeahead } from "../catalog/ProductTypeahead";
import { UnitSelect } from "../catalog/UnitSelect";
import { Alert, Button, Field } from "../ui";

/** What an entry line needs to know about its product. */
export interface ProductRef {
  id: string;
  name: string;
  brand: string | null;
  pack_qty: string | null;
  pack_unit: string | null;
  ingredient?: { id: string; name: string; canonical_unit: CanonicalUnit };
}

export function productRefFromHit(hit: SearchHit): ProductRef {
  return {
    id: hit.id,
    name: hit.name,
    brand: hit.brand,
    pack_qty: hit.pack_qty,
    pack_unit: hit.pack_unit,
    ingredient: { id: hit.ingredient.id, name: hit.ingredient.name, canonical_unit: hit.ingredient.canonical_unit },
  };
}

export function productRefFromProduct(p: Product): ProductRef {
  return {
    id: p.id,
    name: p.name,
    brand: p.brand,
    pack_qty: p.pack_qty,
    pack_unit: p.pack_unit,
    ingredient: { id: p.ingredient.id, name: p.ingredient.name, canonical_unit: p.ingredient.canonical_unit },
  };
}

interface ProductPickerProps {
  id: string;
  label?: string;
  hideLabel?: boolean;
  value: ProductRef | null;
  onChange: (product: ProductRef | null) => void;
  /** Called after a product is chosen or created, e.g. to move focus on. */
  onPicked?: (product: ProductRef) => void;
  disabled?: boolean;
  autoFocus?: boolean;
  inputRef?: RefObject<HTMLInputElement | null>;
  hint?: string;
}

/**
 * Choose a product by typeahead, or create one (and its ingredient) inline.
 * Once chosen the product is shown as text with a Change button. The creation
 * form is not a <form>, so it can sit inside the purchase form without nesting.
 */
export function ProductPicker({
  id,
  label = "Product",
  hideLabel = false,
  value,
  onChange,
  onPicked,
  disabled,
  autoFocus,
  inputRef,
  hint,
}: ProductPickerProps) {
  const [creating, setCreating] = useState(false);

  const pick = (product: ProductRef) => {
    setCreating(false);
    onChange(product);
    onPicked?.(product);
  };

  if (value) {
    return (
      <div className="flex flex-col gap-1">
        <span className={hideLabel ? "sr-only" : labelClass} id={`${id}-label`}>
          {label}
        </span>
        <div
          className="flex min-h-10 flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-300 bg-neutral-50 px-3 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          role="group"
          aria-labelledby={`${id}-label`}
        >
          <span data-testid={`${id}-choice`} className="min-w-0">
            <span className="font-medium">{productTitle(value)}</span>
            {formatPack(value.pack_qty, value.pack_unit) ? (
              <span className="text-neutral-600 dark:text-neutral-400"> · {formatPack(value.pack_qty, value.pack_unit)}</span>
            ) : null}
          </span>
          <Button variant="ghost" className="min-h-8 px-2" disabled={disabled} onClick={() => onChange(null)}>
            Change
          </Button>
        </div>
      </div>
    );
  }

  if (creating) {
    return <InlineProductCreate id={`${id}-new`} onCreated={pick} onCancel={() => setCreating(false)} disabled={disabled} />;
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1">
          <ProductTypeahead
            id={id}
            label={label}
            hideLabel={hideLabel}
            placeholder="Type to search products"
            hint={hint}
            onSelect={(hit) => pick(productRefFromHit(hit))}
            autoFocus={autoFocus}
            inputRef={inputRef}
            disabled={disabled}
          />
        </div>
        <Button variant="secondary" disabled={disabled} onClick={() => setCreating(true)} className="whitespace-nowrap">
          New product
        </Button>
      </div>
    </div>
  );
}

interface InlineProductCreateProps {
  id: string;
  onCreated: (product: ProductRef) => void;
  onCancel: () => void;
  disabled?: boolean;
}

/**
 * The few product fields that matter at the shelf or the market: ingredient
 * (existing or new by name), brand, name, and pack. Enter creates; Escape
 * cancels. Everything else about the product can be edited later.
 */
export function InlineProductCreate({ id, onCreated, onCancel, disabled }: InlineProductCreateProps) {
  const create = useCreateProduct();
  const [ingredient, setIngredient] = useState<IngredientChoice | null>(null);
  const [brand, setBrand] = useState("");
  const [name, setName] = useState("");
  const [packQty, setPackQty] = useState("");
  const [packUnit, setPackUnit] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const busy = disabled || create.isPending;

  const submit = () => {
    if (!ingredient) return setInvalid("Choose an ingredient, or create a new one by name.");
    if (!name.trim()) return setInvalid("A product name is required.");
    const hasQty = packQty.trim() !== "";
    if (hasQty !== (packUnit !== "")) return setInvalid("Give both a pack quantity and a pack unit, or neither.");
    if (hasQty && !isPositiveDecimal(packQty)) return setInvalid("Pack quantity must be a positive number.");
    setInvalid(null);

    const input: ProductCreateInput = { name: name.trim() };
    if (ingredient.kind === "existing") input.ingredient_id = ingredient.ingredient.id;
    else input.ingredient = { name: ingredient.name };
    if (brand.trim()) input.brand = brand.trim();
    if (hasQty) {
      input.pack_qty = packQty.trim();
      input.pack_unit = packUnit;
    }
    create.mutate(input, { onSuccess: (product) => onCreated(productRefFromProduct(product)) });
  };

  // Keys never reach the surrounding form: Enter here creates the product
  // rather than committing a line or saving the purchase.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onCancel();
      return;
    }
    if (event.key !== "Enter") return;
    event.stopPropagation();
    if (event.isDefaultPrevented()) return; // the ingredient box chose an option
    const target = event.target as HTMLElement;
    if (target.tagName === "BUTTON" || target.getAttribute("role") === "combobox") return;
    event.preventDefault();
    if (!busy) submit();
  };

  return (
    <div
      role="group"
      aria-label="New product"
      onKeyDown={onKeyDown}
      className="flex flex-col gap-3 rounded-md border border-neutral-300 bg-neutral-100 p-3 dark:border-neutral-700 dark:bg-neutral-900"
    >
      <p className="text-sm font-medium">
        New product <Badge>created with this entry</Badge>
      </p>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {create.error ? <Alert tone="error">{catalogErrorMessage(create.error)}</Alert> : null}
      <IngredientPicker id={`${id}-ingredient`} value={ingredient} onChange={setIngredient} disabled={busy} />
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id={`${id}-brand`} label="Brand" autoComplete="off" value={brand} onChange={(e) => setBrand(e.target.value)} disabled={busy} />
        <Field
          id={`${id}-name`}
          label="Name"
          autoComplete="off"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          id={`${id}-pack-qty`}
          label="Pack quantity"
          inputMode="decimal"
          autoComplete="off"
          value={packQty}
          onChange={(e) => setPackQty(e.target.value)}
          disabled={busy}
          hint="Both pack fields or neither."
        />
        <UnitSelect id={`${id}-pack-unit`} label="Pack unit" value={packUnit} onChange={setPackUnit} emptyLabel="No pack" disabled={busy} />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button onClick={submit} disabled={busy}>
          {create.isPending ? "Creating…" : "Create product"}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
