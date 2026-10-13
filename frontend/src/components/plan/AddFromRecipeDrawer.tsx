import { useState, type FormEvent } from "react";
import { errorMessage } from "../../api/client";
import { servingsText, useRecipes, type RecipeListItem } from "../../api/recipes";
import { useAddFromRecipe, type FromRecipeOut } from "../../api/shoppingList";
import { Drawer } from "../Drawer";
import { Alert, Button, Field } from "../ui";
import { positiveAmount } from "./amounts";
import { RecipePicker } from "./RecipePicker";

interface AddFromRecipeDrawerProps {
  homeBaseId: string;
  onClose: () => void;
  onDone: (out: FromRecipeOut, recipe: RecipeListItem) => void;
}

/**
 * "Add from recipe" (10, Plan; UI-8.5): a recipe picker and a Scale field, then
 * the recipe's lines are added at that scale. A recipe already on the list
 * changes nothing and says so, with Replace to add it again at this scale.
 */
export function AddFromRecipeDrawer({ homeBaseId, onClose, onDone }: AddFromRecipeDrawerProps) {
  const recipes = useRecipes({});
  const add = useAddFromRecipe();
  const [recipe, setRecipe] = useState<RecipeListItem | null>(null);
  const [scale, setScale] = useState("1");
  const [invalid, setInvalid] = useState<string | null>(null);
  const [alreadyOn, setAlreadyOn] = useState<number | null>(null);
  const formId = "add-from-recipe";
  const servings = recipe ? servingsText(recipe) : null;

  const submit = (replace: boolean) => {
    if (!recipe) {
      setInvalid("Choose a recipe.");
      return;
    }
    const factor = positiveAmount(scale);
    if (!factor) {
      setInvalid("Enter the scale as a number above zero.");
      return;
    }
    setInvalid(null);
    add.mutate(
      { home_base_id: homeBaseId, recipe_id: recipe.id, scale: factor, replace },
      {
        onSuccess: (out) => {
          if (out.already_on_list && !replace) {
            setAlreadyOn(out.existing_items);
            return;
          }
          onDone(out, recipe);
        },
      },
    );
  };
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit(false);
  };

  return (
    <Drawer
      title="Add from recipe"
      thing="recipe"
      dirty={recipe !== null}
      onClose={onClose}
      formId={formId}
      primaryLabel="Add to list"
      busy={add.isPending}
      busyLabel="Adding…"
      primaryDisabled={alreadyOn !== null}
    >
      <form id={formId} onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        <RecipePicker
          id={`${formId}-recipe`}
          recipes={(recipes.data?.items ?? []).filter((r) => r.status !== "missing")}
          loading={recipes.isPending}
          error={recipes.isError ? errorMessage(recipes.error) : null}
          value={recipe}
          onChange={(r) => {
            setRecipe(r);
            setAlreadyOn(null);
          }}
        />
        <Field
          id={`${formId}-scale`}
          label="Scale"
          inputMode="decimal"
          autoComplete="off"
          className="w-32"
          value={scale}
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
        {alreadyOn !== null && recipe ? (
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
      </form>
    </Drawer>
  );
}

/** The Notice after a recipe was added: how many were added, merged and skipped. */
export function fromRecipeSentence(out: FromRecipeOut, title: string): string {
  const parts = [`Added ${out.added} ${out.added === 1 ? "item" : "items"} from ${title}.`];
  if (out.merged > 0)
    parts.push(
      `${out.merged} merged into ${out.merged === 1 ? "an item" : "items"} already on the list.`,
    );
  if (out.skipped.length > 0)
    parts.push(
      `${out.skipped.length} unmapped ${out.skipped.length === 1 ? "line" : "lines"} skipped.`,
    );
  return parts.join(" ");
}
