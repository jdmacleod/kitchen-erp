import { useState } from "react";
import { Link, useParams } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import {
  catalogErrorMessage,
  productTitle,
  trimDecimal,
  useConfirmDensityOverride,
  useProduct,
  useSetProductActive,
  useUpdateProduct,
  type Product,
  type ProductUpdateInput,
} from "../../api/catalog";
import { Badge, ConfirmedBadge } from "../../components/catalog/fields";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";
import { ProductForm, productValues, validateProductValues, type ProductFormValues } from "./ProductForm";

export function ProductDetailPage() {
  const { id } = useParams<{ id: string }>();
  const product = useProduct(id);
  usePageTitle(product.data ? productTitle(product.data) : "Product");

  if (product.isPending) {
    return (
      <>
        <PageHeader title="Product" />
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      </>
    );
  }
  if (product.isError) {
    const missing = isApiError(product.error) && product.error.status === 404;
    return (
      <>
        <PageHeader title="Product" />
        {missing ? (
          <EmptyState title="No such product">
            <Link to="/products" className={`rounded-md underline ${focusRing}`}>
              Back to products
            </Link>
          </EmptyState>
        ) : (
          <Alert tone="error">{errorMessage(product.error)}</Alert>
        )}
      </>
    );
  }
  return <ProductDetail product={product.data} />;
}

function ProductDetail({ product }: { product: Product }) {
  const setActive = useSetProductActive(product.id);
  const confirmDensity = useConfirmDensityOverride(product.id);

  return (
    <>
      <PageHeader title={productTitle(product)}>
        <Button
          variant={product.active ? "danger" : "secondary"}
          disabled={setActive.isPending}
          onClick={() => setActive.mutate(!product.active)}
        >
          {setActive.isPending ? "Saving…" : product.active ? "Deactivate" : "Activate"}
        </Button>
      </PageHeader>
      <div className="flex flex-col gap-6">
        <p className="flex flex-wrap items-center gap-2 text-sm text-neutral-700 dark:text-neutral-300">
          <Link to="/products" className={`rounded underline ${focusRing}`}>
            All products
          </Link>
          <span aria-hidden="true">·</span>
          <span>
            Ingredient{" "}
            <Link to={`/ingredients/${product.ingredient.id}`} className={`rounded font-medium underline ${focusRing}`}>
              {product.ingredient.name}
            </Link>
          </span>
          <span aria-hidden="true">·</span>
          <span>
            Created <time dateTime={product.created_at}>{formatDateTime(product.created_at)}</time>
          </span>
          {product.active ? <Badge tone="good">active</Badge> : <Badge tone="warn">inactive</Badge>}
        </p>
        {setActive.isError ? <Alert tone="error">{catalogErrorMessage(setActive.error)}</Alert> : null}

        {product.density_override !== null ? (
          <Card>
            <h2 className="mb-1 text-lg font-medium">Density override</h2>
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
              <p data-testid="density-override-summary">
                <span className="font-medium">{trimDecimal(product.density_override)} g/ml</span>
                <span className="text-neutral-600 dark:text-neutral-400"> · {product.density_override_source}</span>{" "}
                <ConfirmedBadge confirmed={product.density_override_confirmed} />
              </p>
              {product.density_override_confirmed ? null : (
                <Button variant="secondary" disabled={confirmDensity.isPending} onClick={() => confirmDensity.mutate()}>
                  {confirmDensity.isPending ? "Confirming…" : "Confirm density override"}
                </Button>
              )}
            </div>
            {confirmDensity.isError ? (
              <Alert tone="error" className="mt-2">
                {catalogErrorMessage(confirmDensity.error)}
              </Alert>
            ) : null}
          </Card>
        ) : null}

        <Card>
          <EditProductForm key={product.updated_at} product={product} />
        </Card>
      </div>
    </>
  );
}

function EditProductForm({ product }: { product: Product }) {
  const update = useUpdateProduct(product.id);
  const [values, setValues] = useState<ProductFormValues>(() => productValues(product));
  const [invalid, setInvalid] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const onSubmit = () => {
    setSaved(false);
    const problem = validateProductValues(values);
    setInvalid(problem);
    if (problem || !values.ingredient || values.ingredient.kind !== "existing") return;

    const input: ProductUpdateInput = {};
    if (values.ingredient.ingredient.id !== product.ingredient.id) input.ingredient_id = values.ingredient.ingredient.id;
    const brand = values.brand.trim() || null;
    if (brand !== product.brand) input.brand = brand;
    if (values.name.trim() !== product.name) input.name = values.name.trim();

    const qty = values.pack_qty.trim();
    if (qty === "" && product.pack_qty !== null) input.clear_pack = true;
    else if (qty !== "" && (qty !== product.pack_qty || values.pack_unit !== product.pack_unit)) {
      input.pack_qty = qty;
      input.pack_unit = values.pack_unit;
    }

    const barcode = values.barcode.trim();
    if (barcode === "" && product.barcode !== null) input.clear_barcode = true;
    else if (barcode !== "" && barcode !== product.barcode) input.barcode = barcode;

    if (values.quality_rating !== product.quality_rating) input.quality_rating = values.quality_rating;
    const notes = values.notes.trim() || null;
    if (notes !== product.notes) input.notes = notes;

    const density = values.density_override.trim();
    if (density === "" && product.density_override !== null) input.clear_density_override = true;
    else if (
      density !== "" &&
      (density !== product.density_override || values.density_override_source !== product.density_override_source)
    ) {
      input.density_override = density;
      input.density_override_source = values.density_override_source;
    }

    if (Object.keys(input).length === 0) {
      setSaved(true);
      return;
    }
    update.mutate(input, { onSuccess: () => setSaved(true) });
  };

  return (
    <ProductForm
      idPrefix="edit-product"
      heading="Edit product"
      mode="edit"
      values={values}
      onChange={(next) => {
        setSaved(false);
        setValues(next);
      }}
      onSubmit={onSubmit}
      invalid={invalid}
      error={update.error}
      busy={update.isPending}
      submitLabel="Save changes"
      busyLabel="Saving…"
      notice={saved ? <Alert tone="success">Saved.</Alert> : null}
      densityHint={
        product.density_override !== null ? (
          <p className="text-xs text-neutral-600 dark:text-neutral-400">
            Clearing the value removes the override. A new value resets its confirmation.
          </p>
        ) : null
      }
    >
      <Button variant="secondary" onClick={() => setValues(productValues(product))}>
        Reset
      </Button>
    </ProductForm>
  );
}
