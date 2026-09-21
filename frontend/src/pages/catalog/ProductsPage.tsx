import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import {
  formatPack,
  productTitle,
  useCreateProduct,
  useIngredient,
  useProducts,
  type IngredientSummary,
  type Product,
  type ProductCreateInput,
} from "../../api/catalog";
import { Badge } from "../../components/catalog/fields";
import type { IngredientChoice } from "../../components/catalog/IngredientPicker";
import { ProductTypeahead } from "../../components/catalog/ProductTypeahead";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";
import { ProductForm, emptyProductValues, validateProductValues, type ProductFormValues } from "./ProductForm";

export function ProductsPage() {
  usePageTitle("Products");
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const presetId = params.get("ingredient_id") ?? undefined;
  // When arriving from an ingredient page, preselect that ingredient once it loads.
  const preset = useIngredient(presetId);
  const presetSummary: IngredientSummary | null = preset.data
    ? { id: preset.data.id, name: preset.data.name, canonical_unit: preset.data.canonical_unit, active: preset.data.active }
    : null;

  return (
    <>
      <PageHeader title="Products" />
      <div className="flex flex-col gap-6">
        <Card>
          <h2 className="mb-3 text-lg font-medium">Find a product</h2>
          <ProductTypeahead
            id="product-search"
            label="Search products"
            onSelect={(hit) => navigate(`/products/${hit.id}`)}
            hint="Type part of a name, brand, or ingredient, or scan a barcode. Arrow keys move, Enter opens."
          />
        </Card>

        <Card>
          {presetId && preset.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading ingredient…
            </p>
          ) : (
            <CreateProductForm
              key={presetSummary?.id ?? "none"}
              initialIngredient={presetSummary ? { kind: "existing", ingredient: presetSummary } : null}
            />
          )}
        </Card>

        <RecentProducts />
      </div>
    </>
  );
}

function CreateProductForm({ initialIngredient }: { initialIngredient: IngredientChoice | null }) {
  const create = useCreateProduct();
  const [values, setValues] = useState<ProductFormValues>(() => emptyProductValues(initialIngredient));
  const [invalid, setInvalid] = useState<string | null>(null);
  const [created, setCreated] = useState<Product | null>(null);

  const onSubmit = () => {
    setCreated(null);
    const problem = validateProductValues(values);
    setInvalid(problem);
    if (problem || !values.ingredient) return;

    const input: ProductCreateInput = { name: values.name.trim() };
    if (values.ingredient.kind === "existing") input.ingredient_id = values.ingredient.ingredient.id;
    else input.ingredient = { name: values.ingredient.name };
    if (values.brand.trim()) input.brand = values.brand.trim();
    if (values.pack_qty.trim()) {
      input.pack_qty = values.pack_qty.trim();
      input.pack_unit = values.pack_unit;
    }
    if (values.barcode.trim()) input.barcode = values.barcode.trim();
    if (values.quality_rating !== null) input.quality_rating = values.quality_rating;
    if (values.notes.trim()) input.notes = values.notes.trim();
    if (values.density_override.trim()) {
      input.density_override = values.density_override.trim();
      input.density_override_source = values.density_override_source;
    }

    create.mutate(input, {
      onSuccess: (product) => {
        setCreated(product);
        // Keep the ingredient so several products of one ingredient go quickly.
        setValues(emptyProductValues({ kind: "existing", ingredient: product.ingredient }));
        document.getElementById("new-product-brand")?.focus();
      },
    });
  };

  return (
    <ProductForm
      idPrefix="new-product"
      heading="Add a product"
      mode="create"
      values={values}
      onChange={setValues}
      onSubmit={onSubmit}
      invalid={invalid}
      error={create.error}
      busy={create.isPending}
      submitLabel="Create product"
      busyLabel="Creating…"
      notice={
        created ? (
          <Alert tone="success">
            Created{" "}
            <Link to={`/products/${created.id}`} className={`rounded font-medium underline ${focusRing}`}>
              {productTitle(created)}
            </Link>{" "}
            for{" "}
            <Link to={`/ingredients/${created.ingredient.id}`} className={`rounded underline ${focusRing}`}>
              {created.ingredient.name}
            </Link>
            .
          </Alert>
        ) : null
      }
    />
  );
}

function RecentProducts() {
  const [includeInactive, setIncludeInactive] = useState(false);
  const products = useProducts({ includeInactive });
  const items = products.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-medium">All products</h2>
        <label className="inline-flex min-h-10 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
            className={`size-4 ${focusRing}`}
          />
          Show inactive
        </label>
      </div>
      {products.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : products.isError ? (
        <Alert tone="error">{errorMessage(products.error)}</Alert>
      ) : items.length === 0 ? (
        <EmptyState title="No products yet">Products are the packaged forms of ingredients that vendors sell.</EmptyState>
      ) : (
        <>
          <ul aria-label="Products" className="divide-y divide-neutral-200 dark:divide-neutral-800">
            {items.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <Link to={`/products/${p.id}`} className={`rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
                  {productTitle(p)}
                </Link>
                <span className="flex flex-wrap items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
                  <span>{p.ingredient.name}</span>
                  {formatPack(p.pack_qty, p.pack_unit) ? <span>{formatPack(p.pack_qty, p.pack_unit)}</span> : null}
                  {p.barcode ? <span className="font-mono">{p.barcode}</span> : null}
                  {p.active ? null : <Badge tone="warn">inactive</Badge>}
                </span>
              </li>
            ))}
          </ul>
          {products.hasNextPage ? (
            <div className="mt-3">
              <Button variant="secondary" disabled={products.isFetchingNextPage} onClick={() => products.fetchNextPage()}>
                {products.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}
