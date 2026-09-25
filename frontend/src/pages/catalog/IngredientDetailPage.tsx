import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import {
  CANONICAL_UNITS,
  PERISHABILITIES,
  catalogErrorMessage,
  formatPack,
  isPositiveDecimal,
  perishabilityLabel,
  productTitle,
  trimDecimal,
  useIngredient,
  useProducts,
  useSetIngredientActive,
  useUpdateIngredient,
  type CanonicalUnit,
  type Ingredient,
  type IngredientUpdateInput,
  type Perishability,
} from "../../api/catalog";
import { BridgeEditor } from "../../components/catalog/BridgeEditor";
import { Badge, RadioGroup, SelectField, TextAreaField } from "../../components/catalog/fields";
import { TestBench } from "../../components/catalog/TestBench";
import { IngredientOffers } from "../../components/pricebook/IngredientOffers";
import { IngredientSummary } from "../../components/pricebook/IngredientSummary";
import { useIngredientOffers, useIngredientPriceHistory, type PriceFilters as PriceFiltersValue } from "../../api/pricebook";
import { Alert, Button, Card, EmptyState, Field, PageHeader, focusRing, primaryLinkClass, secondaryLinkClass } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";
import { CategoryChip } from "../../components/CategoryChip";

export function IngredientDetailPage() {
  const { id } = useParams<{ id: string }>();
  const ingredient = useIngredient(id);
  usePageTitle(ingredient.data?.name ?? "Ingredient");

  if (ingredient.isPending) {
    return (
      <>
        <PageHeader title="Ingredient" />
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      </>
    );
  }
  if (ingredient.isError) {
    const missing = isApiError(ingredient.error) && ingredient.error.status === 404;
    return (
      <>
        <PageHeader title="Ingredient" />
        {missing ? (
          <EmptyState title="No such ingredient">
            <Link to="/catalog/ingredients" className={`rounded-md underline ${focusRing}`}>
              Back to ingredients
            </Link>
          </EmptyState>
        ) : (
          <Alert tone="error">{errorMessage(ingredient.error)}</Alert>
        )}
      </>
    );
  }
  return <IngredientDetail ingredient={ingredient.data} />;
}

const NO_FILTERS: PriceFiltersValue = { min_quality: "", exclude_stale: false, exclude_promo: false };
const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * The ingredient hub (docs/spec/10), where search results and inbox items land
 * most often: what it costs where, then the tools that make prices comparable.
 */
function IngredientDetail({ ingredient }: { ingredient: Ingredient }) {
  const setActive = useSetIngredientActive(ingredient.id);
  const [editing, setEditing] = useState(false);
  const [filters, setFilters] = useState<PriceFiltersValue>(NO_FILTERS);
  const offers = useIngredientOffers(ingredient.id, filters);
  const items = offers.data?.items ?? [];
  const unfiltered = JSON.stringify(filters) === JSON.stringify(NO_FILTERS);
  const history = useIngredientPriceHistory(ingredient.id, 90);
  // "No prices yet" only when nothing was recorded at all: no current offer, and
  // nothing in the history (a deactivated location's prices stay in the history).
  const noPrices = offers.isSuccess && history.isSuccess && items.length === 0 && unfiltered && history.data.points.length === 0;

  const meta = (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <CategoryChip category={ingredient.category} categoryKey={ingredient.category_key} />
      <span>Measured in {ingredient.canonical_unit}</span>
      <span aria-hidden="true">·</span>
      <span>{perishabilityLabel[ingredient.perishability]}</span>
      {trimDecimal(ingredient.yield_pct) !== "1" ? (
        <>
          <span aria-hidden="true">·</span>
          <span>yield {trimDecimal(ingredient.yield_pct)}</span>
        </>
      ) : null}
      {ingredient.active ? null : <Badge tone="warn">inactive</Badge>}
    </span>
  );

  return (
    <>
      <nav aria-label="Breadcrumb" className={`mb-2 text-sm ${muted}`}>
        <span>Catalog</span> <span aria-hidden="true">/</span>{" "}
        <Link to="/catalog/ingredients" className={`inline-flex min-h-11 items-center rounded underline lg:min-h-0 ${focusRing}`}>
          Ingredients
        </Link>
      </nav>
      <PageHeader title={ingredient.name} description={meta}>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
            {editing ? "Close editor" : "Edit details"}
          </Button>
          <Button
            variant={ingredient.active ? "danger" : "secondary"}
            disabled={setActive.isPending}
            onClick={() => setActive.mutate(!ingredient.active)}
          >
            {setActive.isPending ? "Saving…" : ingredient.active ? "Deactivate" : "Activate"}
          </Button>
          <Link to="/shop/shelf-prices" className={primaryLinkClass}>
            Log shelf price
          </Link>
        </div>
      </PageHeader>

      <div className="flex flex-col gap-6">
        {setActive.isError ? <Alert tone="error">{catalogErrorMessage(setActive.error)}</Alert> : null}
        {ingredient.notes ? <p className="text-sm whitespace-pre-wrap">{ingredient.notes}</p> : null}
        {editing ? <EditDetailsForm ingredient={ingredient} onDone={() => setEditing(false)} /> : null}

        {offers.isPending ? (
          <p role="status" className={`text-sm ${muted}`}>
            Loading prices…
          </p>
        ) : offers.isError ? (
          <Alert tone="error">{errorMessage(offers.error)}</Alert>
        ) : noPrices ? (
          <EmptyState
            title="No prices yet"
            action={
              <Link to="/shop/shelf-prices" className={primaryLinkClass}>
                Log shelf price
              </Link>
            }
          >
            A price seen on a shelf or paid on a receipt shows up here, with the cheapest first.
          </EmptyState>
        ) : (
          <IngredientSummary ingredient={ingredient} history={history} />
        )}

        <div className="grid gap-6 lg:grid-cols-[1.65fr_1fr]">
          <div className="min-w-0">
            {offers.isSuccess && !noPrices ? (
              <IngredientOffers
                ingredient={ingredient}
                offers={items}
                filters={filters}
                onFilters={setFilters}
                staleThreshold={offers.data.stale_thresholds[ingredient.perishability]}
              />
            ) : null}
          </div>
          <IngredientProducts ingredient={ingredient} />
        </div>

        <BridgeEditor ingredient={ingredient} />
        <TestBench ingredient={ingredient} />
      </div>
    </>
  );
}

function EditDetailsForm({ ingredient, onDone }: { ingredient: Ingredient; onDone: () => void }) {
  const update = useUpdateIngredient(ingredient.id);
  const [form, setForm] = useState({
    name: ingredient.name,
    category: ingredient.category ?? "",
    canonical_unit: ingredient.canonical_unit as CanonicalUnit,
    yield_pct: ingredient.yield_pct,
    perishability: ingredient.perishability as Perishability,
    notes: ingredient.notes ?? "",
  });
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }));

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    if (!isPositiveDecimal(form.yield_pct)) {
      setInvalid("Yield must be a positive fraction such as 0.85.");
      return;
    }
    setInvalid(null);
    const input: IngredientUpdateInput = {};
    if (form.name.trim() !== ingredient.name) input.name = form.name.trim();
    if ((form.category.trim() || null) !== ingredient.category) input.category = form.category.trim() || null;
    if (form.canonical_unit !== ingredient.canonical_unit) input.canonical_unit = form.canonical_unit;
    if (form.yield_pct.trim() !== ingredient.yield_pct) input.yield_pct = form.yield_pct.trim();
    if (form.perishability !== ingredient.perishability) input.perishability = form.perishability;
    if ((form.notes.trim() || null) !== ingredient.notes) input.notes = form.notes.trim() || null;
    if (Object.keys(input).length === 0) {
      onDone();
      return;
    }
    update.mutate(input, { onSuccess: onDone });
  };

  return (
    <Card>
      <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="edit-ingredient-heading" noValidate>
        <h2 id="edit-ingredient-heading" className="text-lg font-medium">
          Edit details
        </h2>
        {invalid ? <Alert tone="error">{invalid}</Alert> : null}
        {update.isError ? <Alert tone="error">{catalogErrorMessage(update.error)}</Alert> : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="edit-name" label="Name" autoComplete="off" required value={form.name} onChange={(e) => set("name", e.target.value)} />
          <Field
            id="edit-category"
            label="Category"
            autoComplete="off"
            value={form.category}
            onChange={(e) => set("category", e.target.value)}
          />
        </div>
        <RadioGroup
          name="edit-unit"
          legend="Canonical unit"
          options={CANONICAL_UNITS.map((u) => ({ value: u, label: u }))}
          value={form.canonical_unit}
          onChange={(v) => set("canonical_unit", v)}
          hint={
            ingredient.measures.length > 0
              ? "Measures are stored in the canonical unit; changing it does not rescale them."
              : undefined
          }
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            id="edit-yield"
            label="Yield (fraction)"
            inputMode="decimal"
            autoComplete="off"
            value={form.yield_pct}
            onChange={(e) => set("yield_pct", e.target.value)}
            hint="Usable part after trimming, between 0 and 1."
          />
          <SelectField
            id="edit-perishability"
            label="Perishability"
            value={form.perishability}
            onChange={(e) => set("perishability", e.target.value as Perishability)}
          >
            {PERISHABILITIES.map((p) => (
              <option key={p} value={p}>
                {perishabilityLabel[p]}
              </option>
            ))}
          </SelectField>
        </div>
        <TextAreaField id="edit-notes" label="Notes" value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save details"}
          </Button>
          <Button variant="secondary" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}

function IngredientProducts({ ingredient }: { ingredient: Ingredient }) {
  const [includeInactive, setIncludeInactive] = useState(false);
  const products = useProducts({ ingredientId: ingredient.id, includeInactive });
  const items = products.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <section aria-labelledby="ingredient-products" className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <h2 id="ingredient-products" className="font-display mb-3 text-lg">
        Products
      </h2>
      {products.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : products.isError ? (
        <Alert tone="error">{errorMessage(products.error)}</Alert>
      ) : items.length === 0 ? (
        <p className={`text-sm ${muted}`}>No products for this ingredient yet.</p>
      ) : (
        <ul aria-label="Products of this ingredient" className="flex flex-wrap gap-2">
          {items.map((p) => (
            <li key={p.id}>
              <Link
                to={`/catalog/products/${p.id}`}
                className={`inline-flex min-h-11 items-center gap-2 rounded-full border border-neutral-300 px-3 text-sm text-neutral-900 hover:bg-neutral-100 lg:min-h-9 dark:border-neutral-700 dark:text-neutral-100 dark:hover:bg-neutral-800 ${focusRing}`}
              >
                {productTitle(p)}
                {formatPack(p.pack_qty, p.pack_unit) ? <span className={`text-xs ${muted}`}>{formatPack(p.pack_qty, p.pack_unit)}</span> : null}
                {p.active ? null : <Badge tone="warn">inactive</Badge>}
              </Link>
            </li>
          ))}
        </ul>
      )}
      {products.hasNextPage ? (
        <Button variant="secondary" className="mt-3" disabled={products.isFetchingNextPage} onClick={() => products.fetchNextPage()}>
          {products.isFetchingNextPage ? "Loading…" : "Load more"}
        </Button>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-9">
          <input type="checkbox" checked={includeInactive} onChange={(e) => setIncludeInactive(e.target.checked)} className={`size-4 ${focusRing}`} />
          Show inactive
        </label>
        <Link to={`/catalog/products?ingredient_id=${encodeURIComponent(ingredient.id)}`} className={secondaryLinkClass}>
          Add a product
        </Link>
      </div>
    </section>
  );
}
