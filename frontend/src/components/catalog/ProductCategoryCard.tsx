import { useState } from "react";
import { catalogErrorMessage, useUpdateIngredient, type Product } from "../../api/catalog";
import { Alert, Card } from "../ui";
import { CategoryField } from "./CategoryField";

/**
 * The category a product shows, which belongs to its ingredient: set here without
 * leaving the product, it applies to every product of that ingredient.
 */
export function ProductCategoryCard({ product }: { product: Product }) {
  const update = useUpdateIngredient(product.ingredient.id);
  const [value, setValue] = useState(product.ingredient.category ?? "");
  const [saved, setSaved] = useState(false);
  const save = (next: string) => {
    setValue(next);
    setSaved(false);
    // Free text ("Other…") is saved as it is typed, like the ingredient's own form.
    update.mutate(next.trim() ? { category: next.trim() } : { category: null }, {
      onSuccess: () => setSaved(true),
    });
  };
  return (
    <Card>
      <h2 className="mb-1 text-lg font-medium">Category</h2>
      <CategoryField
        id={`product-category-${product.id}`}
        value={value}
        onChange={save}
        hint={`The category of ${product.ingredient.name}, so it applies to every product of it.`}
      />
      {saved ? (
        <p role="status" className="mt-2 text-sm">
          Saved.
        </p>
      ) : null}
      {update.error ? <Alert tone="error">{catalogErrorMessage(update.error)}</Alert> : null}
    </Card>
  );
}
