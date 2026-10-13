import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { servingsText, type Recipe } from "../../api/recipes";
import { useAddFromRecipe, useAddItem } from "../../api/shoppingList";
import { useCurrentKitchen, type CurrentKitchen } from "../../lib/kitchen";
import { useNotice } from "../Notice";
import { Alert, Button, Card, Field, focusRing } from "../ui";
import { fromRecipeSentence } from "./AddFromRecipeDrawer";
import { AmountRows, type AmountRow } from "./AmountRows";
import { amountsInput, halfFilledAmount, positiveAmount } from "./amounts";
import { ItemFlags } from "./ItemFlags";
import { KitchenTag } from "./KitchenTag";

/**
 * "Add to list" on an ingredient's and a recipe's page (10, Plan; UI-8.4):
 * small inline panels under the header that name the current kitchen, so a
 * person at the wrong kitchen sees it before adding, confirmed through the
 * Notice "Added … to the {kitchen} list · Open list".
 */

function KitchenLine({ kitchen }: { kitchen: CurrentKitchen }) {
  if (!kitchen.ready)
    return <p className="text-sm text-neutral-600 dark:text-neutral-400">Finding your kitchen…</p>;
  if (!kitchen.currentId) {
    return (
      <Alert tone="info">
        No kitchen yet. Add one under{" "}
        <Link to="/settings/kitchens" className={`rounded font-medium underline ${focusRing}`}>
          Settings → Kitchens
        </Link>{" "}
        and the list opens there.
      </Alert>
    );
  }
  return (
    <p className="text-sm">
      Adding to the <KitchenTag name={kitchen.current?.name ?? "kitchen"} /> list.
    </p>
  );
}

export function AddIngredientToListPanel({
  ingredient,
  onClose,
}: {
  ingredient: { id: string; name: string };
  onClose: () => void;
}) {
  const kitchen = useCurrentKitchen();
  const add = useAddItem();
  const notice = useNotice();
  const [amounts, setAmounts] = useState<AmountRow[]>([{ amount: "", unit: "" }]);
  const [deferrable, setDeferrable] = useState(false);
  const [minQuality, setMinQuality] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const formId = `add-${ingredient.id}-to-list`;
  const name = kitchen.current?.name ?? "kitchen";

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!kitchen.currentId) return;
    const problem = halfFilledAmount(amounts);
    if (problem) {
      setInvalid(problem);
      return;
    }
    setInvalid(null);
    add.mutate(
      {
        home_base_id: kitchen.currentId,
        kind: "ingredient",
        ingredient_id: ingredient.id,
        amounts: amountsInput(amounts),
        deferrable,
        ...(minQuality ? { min_quality: Number(minQuality) } : {}),
        added_from: "ingredient",
      },
      {
        onSuccess: () => {
          onClose();
          notice.show({
            tone: "success",
            message: `Added ${ingredient.name} to the ${name} list.`,
            action: { label: "Open list", to: "/plan/list" },
            focusAction: true,
          });
        },
      },
    );
  };

  return (
    <Card>
      <h2 className="mb-3 text-lg font-medium">Add {ingredient.name} to the list</h2>
      <form
        id={formId}
        onSubmit={onSubmit}
        noValidate
        className="flex flex-col gap-3"
        aria-label="Add to list"
      >
        <KitchenLine kitchen={kitchen} />
        <AmountRows
          idPrefix={formId}
          rows={amounts}
          onChange={setAmounts}
          disabled={!kitchen.currentId}
        />
        <ItemFlags
          idPrefix={formId}
          kind="ingredient"
          deferrable={deferrable}
          minQuality={minQuality}
          onDeferrable={setDeferrable}
          onMinQuality={setMinQuality}
          disabled={!kitchen.currentId}
        />
        {invalid ? <Alert tone="error">{invalid}</Alert> : null}
        {add.isError ? (
          <Alert tone="error">Couldn&apos;t add it: {errorMessage(add.error)}</Alert>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!kitchen.currentId || add.isPending}>
            {add.isPending ? "Adding…" : "Add"}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}

export function AddRecipeToListPanel({
  recipe,
  onClose,
}: {
  recipe: Pick<Recipe, "id" | "title" | "servings" | "servings_text">;
  onClose: () => void;
}) {
  const kitchen = useCurrentKitchen();
  const add = useAddFromRecipe();
  const notice = useNotice();
  const [scale, setScale] = useState("1");
  const [invalid, setInvalid] = useState<string | null>(null);
  const [alreadyOn, setAlreadyOn] = useState<number | null>(null);
  const formId = `add-${recipe.id}-to-list`;
  const name = kitchen.current?.name ?? "kitchen";
  const servings = servingsText(recipe);

  const submit = (replace: boolean) => {
    if (!kitchen.currentId) return;
    const factor = positiveAmount(scale);
    if (!factor) {
      setInvalid("Enter the scale as a number above zero.");
      return;
    }
    setInvalid(null);
    add.mutate(
      { home_base_id: kitchen.currentId, recipe_id: recipe.id, scale: factor, replace },
      {
        onSuccess: (out) => {
          if (out.already_on_list && !replace) {
            setAlreadyOn(out.existing_items);
            return;
          }
          onClose();
          notice.show({
            tone: "success",
            message: `${fromRecipeSentence(out, recipe.title).replace(/\.$/, "")} to the ${name} list.`,
            action:
              out.skipped.length > 0
                ? { label: "Resolve them", to: "/cook/recipes/resolve" }
                : { label: "Open list", to: "/plan/list" },
            focusAction: true,
          });
        },
      },
    );
  };

  return (
    <Card>
      <h2 className="mb-3 text-lg font-medium">Add {recipe.title} to the list</h2>
      <form
        id={formId}
        onSubmit={(e) => {
          e.preventDefault();
          submit(false);
        }}
        noValidate
        className="flex flex-col gap-3"
        aria-label="Add to list"
      >
        <KitchenLine kitchen={kitchen} />
        <Field
          id={`${formId}-scale`}
          label="Scale"
          inputMode="decimal"
          autoComplete="off"
          className="w-32"
          value={scale}
          disabled={!kitchen.currentId}
          hint={
            servings
              ? `${servings} servings · ×${scale.trim() || "1"}`
              : "×1 adds the recipe as written."
          }
          onChange={(e) => {
            setScale(e.target.value);
            setAlreadyOn(null);
          }}
        />
        {alreadyOn !== null ? (
          <Alert tone="info">
            <span className="flex flex-col gap-2">
              <span data-testid="already-on-list">
                {recipe.title} is already on the list: {alreadyOn}{" "}
                {alreadyOn === 1 ? "item" : "items"} to get. Nothing was changed.
              </span>
              <span>
                <Button variant="secondary" disabled={add.isPending} onClick={() => submit(true)}>
                  {add.isPending ? "Replacing…" : `Replace at ×${scale.trim() || "1"}`}
                </Button>
              </span>
            </span>
          </Alert>
        ) : null}
        {invalid ? <Alert tone="error">{invalid}</Alert> : null}
        {add.isError ? (
          <Alert tone="error">Couldn&apos;t add the recipe: {errorMessage(add.error)}</Alert>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button
            type="submit"
            disabled={!kitchen.currentId || add.isPending || alreadyOn !== null}
          >
            {add.isPending && alreadyOn === null ? "Adding…" : "Add to list"}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}
