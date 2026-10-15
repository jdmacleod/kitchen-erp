import { useMutation, useQueryClient } from "@tanstack/react-query";
import { catalogKeys, useIngredient, type Ingredient } from "../../api/catalog";
import { api, errorMessage } from "../../api/client";
import { useHealth } from "../../api/queries";
import { useIngredientRecipes } from "../../api/recipes";
import { Alert, Button } from "../ui";

const trim = (value: string) => Number(value).toString();

/**
 * "Measure in ml instead" on a no-density row whose ingredient is in g but is
 * only ever sold by volume (issue 358). It says what the change does before
 * saving: the prices are recomputed per ml, measures stored in g are removed
 * (they can't be converted without a density), and recipes that weigh the
 * ingredient will need one. Saving is the ingredient's ordinary unit change.
 */
export function MlSwitchPanel({
  ingredientId,
  name,
  onDone,
  onCancel,
}: {
  ingredientId: string;
  name: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const client = useQueryClient();
  const ingredient = useIngredient(ingredientId);
  const cook = useHealth().data?.features?.includes("cook") ?? false;
  const recipes = useIngredientRecipes(ingredientId, cook);
  const measures = ingredient.data?.measures ?? [];
  const switchUnit = useMutation({
    mutationFn: () =>
      api<Ingredient>(`/ingredients/${encodeURIComponent(ingredientId)}`, {
        method: "PATCH",
        body: { canonical_unit: "ml", drop_measures: measures.length > 0 },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["price-book"] });
      void client.invalidateQueries({ queryKey: catalogKeys.ingredients });
      void client.invalidateQueries({ queryKey: ["inbox"] });
      void client.invalidateQueries({ queryKey: ["bridges"] });
      onDone();
    },
  });
  const used = recipes.data?.items ?? [];

  return (
    <div
      role="group"
      aria-label={`Measure ${name} in ml`}
      className="flex flex-col gap-2 rounded-md border border-neutral-300 p-3 text-sm dark:border-neutral-700"
    >
      <p>
        {name} will be measured in ml, like the packs it is sold in. Its prices are worked out again
        per ml, so these rows need no density.
      </p>
      {measures.length > 0 ? (
        <p>
          Its {measures.length === 1 ? "measure is" : "measures are"} in g and will be removed:{" "}
          {measures.map((m) => `1 ${m.label} = ${trim(m.canonical_qty)} g`).join(", ")}.
        </p>
      ) : null}
      {used.length > 0 ? (
        <p>
          Used in {used.length === 1 ? "1 recipe" : `${used.length} recipes`}:{" "}
          {used.map((r) => r.title).join(", ")}. Amounts there given by weight will need a density
          to be costed.
        </p>
      ) : null}
      {switchUnit.isError ? <Alert tone="error">{errorMessage(switchUnit.error)}</Alert> : null}
      <div className="grid grid-cols-2 gap-2 lg:flex">
        <Button
          disabled={ingredient.isPending || switchUnit.isPending}
          onClick={() => switchUnit.mutate()}
        >
          Switch to ml
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
