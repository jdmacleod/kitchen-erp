import { useState, type FormEvent, type ReactNode } from "react";
import {
  BRIDGE_SOURCES,
  catalogErrorMessage,
  isPositiveDecimal,
  useUnits,
  type BridgeSource,
  type Product,
  type Unit,
} from "../../api/catalog";
import { Disclosure, SelectField, TextAreaField } from "../../components/catalog/fields";
import { IngredientPicker, type IngredientChoice } from "../../components/catalog/IngredientPicker";
import { RatingInput } from "../../components/catalog/RatingInput";
import { UnitSelect } from "../../components/catalog/UnitSelect";
import { Alert, Button, Field } from "../../components/ui";

export interface ProductFormValues {
  ingredient: IngredientChoice | null;
  brand: string;
  name: string;
  pack_qty: string;
  pack_unit: string;
  barcode: string;
  quality_rating: number | null;
  notes: string;
  density_override: string;
  density_override_source: BridgeSource;
}

export function emptyProductValues(ingredient: IngredientChoice | null = null): ProductFormValues {
  return {
    ingredient,
    brand: "",
    name: "",
    pack_qty: "",
    pack_unit: "",
    barcode: "",
    quality_rating: null,
    notes: "",
    density_override: "",
    density_override_source: "label",
  };
}

export function productValues(p: Product): ProductFormValues {
  return {
    ingredient: { kind: "existing", ingredient: p.ingredient },
    brand: p.brand ?? "",
    name: p.name,
    pack_qty: p.pack_qty ?? "",
    pack_unit: p.pack_unit ?? "",
    barcode: p.barcode ?? "",
    quality_rating: p.quality_rating,
    notes: p.notes ?? "",
    density_override: p.density_override ?? "",
    density_override_source: p.density_override_source ?? "label",
  };
}

/** Client-side checks that mirror the server's pairing rules. Null when valid. */
export function validateProductValues(v: ProductFormValues): string | null {
  if (!v.ingredient) return "Choose an ingredient, or create a new one by name.";
  if (!v.name.trim()) return "A name is required.";
  const hasQty = v.pack_qty.trim() !== "";
  const hasUnit = v.pack_unit !== "";
  if (hasQty !== hasUnit) return "Give both a pack quantity and a pack unit, or neither.";
  if (hasQty && !isPositiveDecimal(v.pack_qty)) return "Pack quantity must be a positive number.";
  if (v.barcode.trim() && v.barcode.trim().length < 4) return "A barcode needs at least 4 characters.";
  if (v.density_override.trim() && !isPositiveDecimal(v.density_override)) {
    return "Density override must be a positive number of grams per millilitre.";
  }
  return null;
}

interface ProductFormProps {
  idPrefix: string;
  heading: string;
  mode: "create" | "edit";
  values: ProductFormValues;
  onChange: (values: ProductFormValues) => void;
  onSubmit: () => void;
  invalid: string | null;
  error: unknown;
  busy: boolean;
  submitLabel: string;
  busyLabel: string;
  /** Extra controls next to the submit button. */
  children?: ReactNode;
  /** A notice rendered above the fields (e.g. success). */
  notice?: ReactNode;
  densityHint?: ReactNode;
  /**
   * "drawer": the drawer supplies the title and the submit button, which submits
   * this form by `formId` from its sticky footer (spec 08, Drawer).
   */
  layout?: "page" | "drawer";
  formId?: string;
}

const CANONICAL_DIMENSION: Record<string, string> = { g: "mass", ml: "volume", each: "count" };

/**
 * Why the density section should open, or null. Only a mass/volume mismatch is a
 * density's job; a count needs a pack or a measure instead (UI-3.5).
 */
export function densityReason(packUnit: string, ingredient: IngredientChoice | null, units: Unit[]): string | null {
  if (!packUnit || !ingredient) return null;
  const canonical = ingredient.kind === "existing" ? ingredient.ingredient.canonical_unit : "g";
  const from = units.find((u) => u.code === packUnit)?.dimension;
  const to = CANONICAL_DIMENSION[canonical];
  if (!from || !to || from === to || from === "count" || to === "count") return null;
  const name = ingredient.kind === "existing" ? ingredient.ingredient.name : ingredient.name;
  return `${packUnit} is a ${from} unit and ${name} is measured in ${canonical}, so this product's prices can't be compared until there is a density. You can add one here or later; saving without one is fine.`;
}

/** The product fields, shared by the create and edit pages. State lives in the page. */
export function ProductForm({
  idPrefix,
  heading,
  mode,
  values,
  onChange,
  onSubmit,
  invalid,
  error,
  busy,
  submitLabel,
  busyLabel,
  children,
  notice,
  densityHint,
  layout = "page",
  formId,
}: ProductFormProps) {
  const units = useUnits();
  const reason = densityReason(values.pack_unit, values.ingredient, units.data ?? []);
  const [densityOpen, setDensityOpen] = useState(values.density_override !== "");
  const set = <K extends keyof ProductFormValues>(key: K, value: ProductFormValues[K]) =>
    onChange({ ...values, [key]: value });
  const headingId = `${idPrefix}-heading`;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit();
  };

  return (
    <form
      id={formId}
      onSubmit={submit}
      className="flex flex-col gap-4"
      aria-labelledby={layout === "page" ? headingId : undefined}
      aria-label={layout === "drawer" ? heading : undefined}
      noValidate
    >
      {layout === "page" ? (
        <h2 id={headingId} className="text-lg font-medium">
          {heading}
        </h2>
      ) : null}
      {notice}
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {error ? <Alert tone="error">{catalogErrorMessage(error)}</Alert> : null}

      <IngredientPicker
        id={`${idPrefix}-ingredient`}
        value={values.ingredient}
        onChange={(choice) => set("ingredient", choice)}
        allowCreate={mode === "create"}
        disabled={busy}
        hint={
          mode === "create"
            ? "The ingredient this product is a packaged form of. A new one can be created here by name."
            : undefined
        }
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          id={`${idPrefix}-brand`}
          label="Brand"
          autoComplete="off"
          value={values.brand}
          onChange={(e) => set("brand", e.target.value)}
        />
        <Field
          id={`${idPrefix}-name`}
          label="Name"
          autoComplete="off"
          required
          value={values.name}
          onChange={(e) => set("name", e.target.value)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          id={`${idPrefix}-pack-qty`}
          label="Pack quantity"
          inputMode="decimal"
          autoComplete="off"
          value={values.pack_qty}
          onChange={(e) => set("pack_qty", e.target.value)}
          hint="Both pack fields or neither."
        />
        <UnitSelect
          id={`${idPrefix}-pack-unit`}
          label="Pack unit"
          value={values.pack_unit}
          onChange={(code) => set("pack_unit", code)}
          emptyLabel="No pack"
        />
      </div>

      <Field
        id={`${idPrefix}-barcode`}
        label="Barcode"
        inputMode="numeric"
        autoComplete="off"
        value={values.barcode}
        onChange={(e) => set("barcode", e.target.value)}
        hint="As printed under the bars. Optional."
      />

      <RatingInput name={`${idPrefix}-rating`} value={values.quality_rating} onChange={(v) => set("quality_rating", v)} />

      <TextAreaField id={`${idPrefix}-notes`} label="Notes" value={values.notes} onChange={(e) => set("notes", e.target.value)} />

      <Disclosure summary="Density override" open={densityOpen || reason !== null} onOpenChange={setDensityOpen}>
        {reason ? (
          <p data-testid="density-reason" className="text-sm text-amber-900 dark:text-amber-200">
            {reason}
          </p>
        ) : null}
        {densityHint}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            id={`${idPrefix}-density-override`}
            label="Density (g/ml)"
            inputMode="decimal"
            autoComplete="off"
            value={values.density_override}
            onChange={(e) => set("density_override", e.target.value)}
            hint="Used instead of the ingredient's density for this product only."
          />
          <SelectField
            id={`${idPrefix}-density-override-source`}
            label="Source"
            value={values.density_override_source}
            onChange={(e) => set("density_override_source", e.target.value as BridgeSource)}
            disabled={values.density_override.trim() === ""}
          >
            {BRIDGE_SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </SelectField>
        </div>
      </Disclosure>

      {layout === "page" ? (
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy}>
            {busy ? busyLabel : submitLabel}
          </Button>
          {children}
        </div>
      ) : null}
    </form>
  );
}
