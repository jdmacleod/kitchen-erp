// "Made by recipe" on the ingredient page (issue 526): the house recipe that makes
// it. A line on the ingredient that no product price covers costs from that
// recipe instead. Unlinked, the section offers the one recipe titled like the
// ingredient, and a search over the recipes; linked, it says what the recipe
// costs per unit it makes, and what is missing when it can't cost anything yet.
// A product price always wins over the recipe.

import { useState } from "react";
import { Link } from "react-router";
import { catalogErrorMessage, type Ingredient } from "../../api/catalog";
import { useHealth } from "../../api/queries";
import { useRecipes, type RecipeListItem } from "../../api/recipes";
import {
  unitCostText,
  useSetMadeBy,
  YIELD_EXAMPLE,
  type IngredientMadeBy,
  type MadeByRecipe as Linked,
  type RecipeLink,
} from "../../api/recipeSources";
import { Combobox } from "../catalog/Combobox";
import { useNotice } from "../Notice";
import { Alert, Button, focusRing, sectionHeading, tapTarget } from "../ui";

const muted = "text-neutral-600 dark:text-neutral-400";
const squash = "text-amber-900 dark:text-amber-200";
const link = `${tapTarget} rounded-sm font-medium underline ${focusRing}`;
const button = "min-h-11 px-3 lg:min-h-9";

function Code({ children }: { children: string }) {
  return <code className="font-mono text-xs">{children}</code>;
}

/** "Linked. 4 recipes recomputed", or with the ones that couldn't be. */
export function linkedSentence(done: IngredientMadeBy, verb: "Linked" | "Unlinked"): string {
  const n = done.recomputed ?? 0;
  const failed = done.recompute_failed ?? 0;
  const recipes = `${n} ${n === 1 ? "recipe" : "recipes"} recomputed`;
  const head =
    failed > 0
      ? `${verb}. ${recipes}, ${failed} couldn't be; run kerp recompute-costs`
      : `${verb}. ${recipes}`;
  const warning = done.warnings?.[0]?.message;
  return warning ? `${head}. ${warning}` : `${head}.`;
}

/** What stops a linked recipe from costing anything yet, or null. */
function LinkedWarning({ recipe }: { recipe: Linked }) {
  let words: React.ReactNode = null;
  if (recipe.status === "missing") {
    words = (
      <>
        <Code>{recipe.path}</Code> is gone, so lines won&apos;t cost from it. Restore it, or link
        another recipe.
      </>
    );
  } else if (recipe.status === "parse_error") {
    words = (
      <>
        {recipe.title} can&apos;t be read; fix <Code>{recipe.path}</Code>, then rescan.
      </>
    );
  } else if (!recipe.makes_ok && recipe.makes_text) {
    words = (
      <>
        {recipe.title} makes <Code>{recipe.makes_text}</Code>, not an amount and a unit, so lines
        won&apos;t cost until you write it as <Code>{YIELD_EXAMPLE}</Code> in{" "}
        <Code>{recipe.path}</Code>, then rescan.
      </>
    );
  } else if (!recipe.makes_ok) {
    words = (
      <>
        {recipe.title} has no amount it makes, so lines won&apos;t cost until you add{" "}
        <Code>{YIELD_EXAMPLE}</Code> to <Code>{recipe.path}</Code>, then rescan.
      </>
    );
  } else if (!recipe.unit_cost) {
    words = <>{recipe.title} has no counted cost yet, so lines from it have none either.</>;
  }
  if (!words) return null;
  return (
    <p className={`text-sm ${squash}`} data-testid="made-by-warning">
      {words}
    </p>
  );
}

function RecipeSearch({
  id,
  disabled,
  onPick,
}: {
  id: string;
  disabled: boolean;
  onPick: (recipe: RecipeListItem) => void;
}) {
  const recipes = useRecipes({});
  const [text, setText] = useState("");
  const q = text.trim().toLocaleLowerCase();
  const all = (recipes.data?.items ?? []).filter((r) => r.status !== "missing");
  const items = q ? all.filter((r) => r.title.toLocaleLowerCase().includes(q)) : all;
  let status: string | undefined;
  if (recipes.isError) status = catalogErrorMessage(recipes.error);
  else if (recipes.isPending) status = "Loading recipes…";
  else if (all.length === 0) status = "No recipes are indexed yet.";
  else if (items.length === 0) status = "No recipes match.";
  return (
    <Combobox<RecipeListItem>
      id={id}
      label="Link a recipe"
      placeholder="Type to search recipes"
      listLabel="Recipes"
      inputValue={text}
      onInputChange={setText}
      items={items}
      getKey={(r) => r.id}
      status={status}
      disabled={disabled}
      onSelect={(r) => {
        setText("");
        onPick(r);
      }}
      renderItem={(r) => (
        <span>
          <span className="font-medium">{r.title}</span>
          {r.status === "parse_error" ? <span className={muted}> · can&apos;t be read</span> : null}
        </span>
      )}
    />
  );
}

/** The section, shown only once the Cook section is built: recipes are Phase 3's. */
export function MadeByRecipe({ ingredient }: { ingredient: Ingredient }) {
  const cook = useHealth().data?.features?.includes("cook") ?? false;
  return cook ? <MadeBy ingredient={ingredient} /> : null;
}

function MadeBy({ ingredient }: { ingredient: IngredientMadeBy }) {
  const update = useSetMadeBy(ingredient.id);
  const notice = useNotice();
  const [error, setError] = useState<string | null>(null);
  const linked = ingredient.made_by_recipe ?? null;
  const offer = ingredient.recipe_match ?? null;

  const setLink = (recipe: Pick<RecipeLink, "id"> | null) => {
    setError(null);
    update.mutate(recipe ? recipe.id : null, {
      onSuccess: (done) =>
        notice.show({
          tone: (done.recompute_failed ?? 0) > 0 || done.warnings?.length ? "info" : "success",
          message: linkedSentence(done, recipe ? "Linked" : "Unlinked"),
          action: recipe
            ? { label: "Used in", to: `/cook/recipes/${recipe.id}#used-in` }
            : undefined,
        }),
      onError: (e) => setError(catalogErrorMessage(e)),
    });
  };

  return (
    <section
      aria-labelledby="made-by-heading"
      className="flex flex-col gap-2"
      data-testid="made-by"
    >
      <h2 id="made-by-heading" className={sectionHeading}>
        Made by recipe
      </h2>
      {linked ? (
        <>
          <p className="flex flex-wrap items-center gap-x-2 text-sm" data-testid="made-by-linked">
            <span>
              Made by{" "}
              <Link to={`/cook/recipes/${linked.id}`} className={link}>
                {linked.title}
              </Link>{" "}
              recipe
              {linked.unit_cost ? (
                <span className={muted}>
                  {" "}
                  · {unitCostText(linked.unit_cost)} when no product price qualifies
                </span>
              ) : null}
            </span>
            <Button
              variant="ghost"
              className={button}
              disabled={update.isPending}
              onClick={() => setLink(null)}
            >
              {update.isPending ? "Clearing…" : "Clear"}
            </Button>
          </p>
          <LinkedWarning recipe={linked} />
        </>
      ) : (
        <>
          <p className={`text-sm ${muted}`}>
            A house-made ingredient can cost from its recipe when no product price is there.
          </p>
          {offer ? (
            <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="made-by-offer">
              <Button
                variant="secondary"
                className={button}
                disabled={update.isPending}
                onClick={() => setLink(offer)}
              >
                {update.isPending ? "Linking…" : `Cost this from the ${offer.title} recipe`}
              </Button>
            </p>
          ) : null}
          <RecipeSearch
            id={`made-by-${ingredient.id}`}
            disabled={update.isPending}
            onPick={(r) => setLink(r)}
          />
          {update.isPending && !offer ? (
            <p role="status" className={`text-sm ${muted}`}>
              Linking…
            </p>
          ) : null}
        </>
      )}
      {error ? <Alert tone="error">{error}</Alert> : null}
    </section>
  );
}
