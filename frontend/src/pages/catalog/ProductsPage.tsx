import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import {
  formatPack,
  ingredientSummary,
  productTitle,
  useCreateProduct,
  useIngredient,
  useProducts,
  type IngredientSummary,
  type Product,
  type ProductCreateInput,
  type ProductListItem,
} from "../../api/catalog";
import { Badge, QualityStars } from "../../components/catalog/fields";
import type { IngredientChoice } from "../../components/catalog/IngredientPicker";
import { CATEGORY_KEYS, CategoryChip, categoryClass, type CategoryKey } from "../../components/CategoryChip";
import { Drawer } from "../../components/Drawer";
import { useNotice } from "../../components/Notice";
import { Alert, Button, EmptyState, PageHeader, focusRing } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { usePageTitle } from "../../lib/usePageTitle";
import { ProductForm, emptyProductValues, validateProductValues, type ProductFormValues } from "./ProductForm";

const categoryLabel = (key: CategoryKey) => key[0].toUpperCase() + key.slice(1);
const isCategory = (value: string | null): value is CategoryKey => CATEGORY_KEYS.includes(value as CategoryKey);
const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * Products (docs/spec/10): search and the catalog first, creation in a drawer.
 * Search, the category and "Show inactive" live in the URL, so Back and a shared
 * link keep them, and they filter on the server (D12).
 */
export function ProductsPage() {
  usePageTitle("Products");
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const category = isCategory(params.get("category")) ? (params.get("category") as CategoryKey) : null;
  const includeInactive = params.get("inactive") === "1";
  const presetId = params.get("ingredient_id") ?? undefined;

  const setParam = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  const [text, setText] = useState(q);
  // The field follows ?q= when it changes from outside (Back, a link), and the
  // URL takes only text the debounce has settled on, so an old pending value
  // cannot be written back over the new one.
  const [seenQ, setSeenQ] = useState(q);
  if (q !== seenQ) {
    setSeenQ(q);
    setText(q);
  }
  const debounced = useDebouncedValue(text, 250);
  useEffect(() => {
    if (debounced === text && debounced.trim() !== q) setParam("q", debounced.trim() || null);
    // Only the typed text drives the URL; q changing on its own (Back) is read below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const products = useProducts({ q, category, includeInactive });
  const items = products.data?.pages.flatMap((p) => p.items) ?? [];

  // Arriving from an ingredient's page opens the drawer with that ingredient.
  const [adding, setAdding] = useState(presetId !== undefined);
  const closeDrawer = () => {
    setAdding(false);
    if (presetId) setParam("ingredient_id", null);
  };

  const notice = useNotice();
  const onCreated = async (product: Product) => {
    closeDrawer();
    await products.refetch();
    // G10: the new row takes focus when it is in the list; otherwise the Notice
    // links to it and its link takes focus.
    requestAnimationFrame(() => {
      const row = document.getElementById(`product-row-${product.id}`);
      if (row) {
        row.focus();
        return;
      }
      notice.show({
        tone: "success",
        message: `Added ${productTitle(product)}.`,
        action: { label: "Open it", to: `/catalog/products/${product.id}` },
        focusAction: true,
      });
    });
  };

  const filtered = q !== "" || category !== null;
  const clearFilters = () => {
    setText("");
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("q");
        next.delete("category");
        return next;
      },
      { replace: true },
    );
  };

  return (
    <>
      <PageHeader title="Products" description="The packaged things vendors sell, each a form of one ingredient.">
        <Button onClick={() => setAdding(true)}>Add product</Button>
      </PageHeader>

      <div className="mb-4 flex flex-col gap-3">
        <label htmlFor="product-filter" className="sr-only">
          Search products
        </label>
        <input
          id="product-filter"
          type="text"
          enterKeyHint="search"
          autoComplete="off"
          maxLength={200}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search by name, brand, ingredient or barcode"
          className={`min-h-12 w-full rounded-lg border border-neutral-300 bg-white px-4 text-base dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div role="group" aria-label="Category" className="flex flex-wrap gap-1">
            <button
              type="button"
              aria-pressed={category === null}
              onClick={() => setParam("category", null)}
              className={`inline-flex min-h-11 items-center rounded-full px-3 text-sm lg:min-h-9 ${focusRing} ${
                category === null ? "bg-neutral-800 font-medium text-white dark:bg-neutral-200 dark:text-neutral-900" : "text-neutral-700 hover:bg-neutral-200 dark:text-neutral-300 dark:hover:bg-neutral-800"
              }`}
            >
              All
            </button>
            {CATEGORY_KEYS.map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={category === key}
                onClick={() => setParam("category", category === key ? null : key)}
                className={`inline-flex min-h-11 items-center rounded-full px-0.5 lg:min-h-9 ${focusRing}`}
              >
                <span
                  className={`${categoryClass(key)} cat-chip ${
                    category === key ? "outline-2 outline-offset-1 outline-neutral-800 dark:outline-neutral-200" : ""
                  }`}
                >
                  {categoryLabel(key)}
                </span>
              </button>
            ))}
          </div>
          <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-9">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setParam("inactive", e.target.checked ? "1" : null)}
              className={`size-4 ${focusRing}`}
            />
            Show inactive
          </label>
        </div>
      </div>

      {products.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : products.isError ? (
        <Alert tone="error">{errorMessage(products.error)}</Alert>
      ) : items.length === 0 ? (
        filtered ? (
          <EmptyState
            title={`No products match${q ? ` ‘${q}’` : ""}${category ? ` in ${categoryLabel(category)}` : ""}`}
            action={
              <Button variant="secondary" onClick={clearFilters}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState title="Add your first product" action={<Button onClick={() => setAdding(true)}>Add product</Button>}>
            Products are the packaged forms of ingredients that vendors sell.
          </EmptyState>
        )
      ) : (
        <section className="rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          <div className="overflow-x-auto">
            <ProductTable items={items} />
          </div>
          {products.hasNextPage ? (
            <div className="border-t border-neutral-200 p-3 dark:border-neutral-800">
              <Button variant="secondary" disabled={products.isFetchingNextPage} onClick={() => products.fetchNextPage()}>
                {products.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          ) : null}
        </section>
      )}

      {adding ? <AddProductDrawer presetId={presetId} onClose={closeDrawer} onCreated={onCreated} /> : null}
    </>
  );
}

function ProductTable({ items }: { items: ProductListItem[] }) {
  const th = `px-4 py-2 text-left text-xs font-semibold ${muted}`;
  return (
    <table aria-label="Products" className="w-full min-w-[44rem] text-sm">
      <thead className="border-b border-neutral-200 dark:border-neutral-800">
        <tr>
          <th scope="col" className={th}>Product</th>
          <th scope="col" className={th}>Ingredient</th>
          <th scope="col" className={th}>Pack</th>
          <th scope="col" className={th}>Quality</th>
          <th scope="col" className={th}>Last paid</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
        {items.map((p) => (
          <tr key={p.id}>
            <td className="px-4 py-2 align-top">
              <Link id={`product-row-${p.id}`} to={`/catalog/products/${p.id}`} className={`rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
                {p.name}
              </Link>
              {p.active ? null : (
                <>
                  {" "}
                  <Badge tone="warn">inactive</Badge>
                </>
              )}
              {p.brand ? <span className={`block ${muted}`}>{p.brand}</span> : null}
            </td>
            <td className="px-4 py-2 align-top">
              <span className="block">{p.ingredient.name}</span>
              <CategoryChip category={p.ingredient.category} categoryKey={p.ingredient.category_key} />
            </td>
            <td className="px-4 py-2 align-top tabular-nums">{formatPack(p.pack_qty, p.pack_unit) || "—"}</td>
            <td className="px-4 py-2 align-top">
              <QualityStars rating={p.quality_rating} />
            </td>
            <td className="px-4 py-2 align-top">
              {p.last_paid ? (
                <>
                  <span className="block tabular-nums">{formatMoney(p.last_paid.price)}</span>
                  <span className={`block ${muted}`}>
                    {p.last_paid.vendor_name} · {formatDate(p.last_paid.paid_at)}
                  </span>
                </>
              ) : (
                <span aria-label="Never paid">—</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AddProductDrawer({
  presetId,
  onClose,
  onCreated,
}: {
  presetId: string | undefined;
  onClose: () => void;
  onCreated: (product: Product) => void;
}) {
  // When arriving from an ingredient page, preselect that ingredient once it loads.
  const preset = useIngredient(presetId);
  const presetSummary: IngredientSummary | null = preset.data ? ingredientSummary(preset.data) : null;
  if (presetId && preset.isPending) {
    return (
      <Drawer title="Add product" thing="product" dirty={false} onClose={onClose} formId="add-product" primaryLabel="Add product" busy>
        <p role="status" className={`text-sm ${muted}`}>
          Loading ingredient…
        </p>
      </Drawer>
    );
  }
  return (
    <AddProductForm
      initialIngredient={presetSummary ? { kind: "existing", ingredient: presetSummary } : null}
      onClose={onClose}
      onCreated={onCreated}
    />
  );
}

function AddProductForm({
  initialIngredient,
  onClose,
  onCreated,
}: {
  initialIngredient: IngredientChoice | null;
  onClose: () => void;
  onCreated: (product: Product) => void;
}) {
  const create = useCreateProduct();
  const [initial] = useState(() => emptyProductValues(initialIngredient));
  const [values, setValues] = useState<ProductFormValues>(initial);
  const [invalid, setInvalid] = useState<string | null>(null);
  // A half-typed ingredient search is typed input too (D5).
  const [ingredientText, setIngredientText] = useState("");
  // Typed input, not a preselected ingredient, is what closing must ask about.
  const dirty = JSON.stringify(values) !== JSON.stringify(initial) || ingredientText.trim() !== "";

  const onSubmit = () => {
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
    create.mutate(input, { onSuccess: onCreated });
  };

  return (
    <Drawer title="Add product" thing="product" dirty={dirty} onClose={onClose} formId="add-product" primaryLabel="Add product" busy={create.isPending} busyLabel="Adding…">
      <ProductForm
        layout="drawer"
        formId="add-product"
        onIngredientText={setIngredientText}
        idPrefix="new-product"
        heading="Add product"
        mode="create"
        values={values}
        onChange={setValues}
        onSubmit={onSubmit}
        invalid={invalid}
        error={create.error}
        busy={create.isPending}
        submitLabel="Add product"
        busyLabel="Adding…"
      />
    </Drawer>
  );
}
