import { useState } from "react";
import { Link } from "react-router";
import { useHealth } from "../../api/queries";
import { useIngredientRecipes, type IngredientRecipeUse } from "../../api/recipes";
import { Button, focusRing, tapTarget } from "../ui";
import { RecipeBadges } from "./RecipeBadges";

const muted = "text-neutral-600 dark:text-neutral-400";
const SHOWN = 10;

/**
 * The ingredient hub's "Used in" card (10; UI-7.14; 07 criterion 33): the
 * recipes whose lines resolve to this ingredient, each a link with the line's
 * quantity as written, up to ten, then "All {n} recipes". Omitted when no recipe
 * uses the ingredient and when the Cook section is not built.
 */
export function UsedInRecipes({ ingredientId }: { ingredientId: string }) {
  const cook = useHealth().data?.features?.includes("cook") ?? false;
  const recipes = useIngredientRecipes(ingredientId, cook);
  const [all, setAll] = useState(false);
  const items = recipes.data?.items ?? [];
  if (!cook || !recipes.isSuccess || items.length === 0) return null;
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <section aria-labelledby="ingredient-used-in" className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900" data-testid="used-in-card">
      <h2 id="ingredient-used-in" className="font-display mb-3 text-lg">
        Used in
      </h2>
      <ul aria-label="Recipes using this ingredient" className="flex flex-col divide-y divide-neutral-200 text-sm dark:divide-neutral-800">
        {shown.map((use) => (
          <UseRow key={use.id} use={use} />
        ))}
      </ul>
      {!all && items.length > SHOWN ? (
        <Button variant="secondary" className="mt-3" onClick={() => setAll(true)}>
          All {recipes.data.total} recipes
        </Button>
      ) : null}
    </section>
  );
}

function UseRow({ use }: { use: IngredientRecipeUse }) {
  const quantities = use.quantities.filter((q): q is string => q !== null);
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2">
      <span className="flex min-w-0 flex-wrap items-center gap-2">
        <Link to={`/cook/recipes/${use.id}`} className={`${tapTarget} rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
          {use.title}
        </Link>
        {use.status !== "ok" ? <RecipeBadges recipe={{ status: use.status, dirty: false }} /> : null}
      </span>
      <span className={`tabular-nums ${muted}`}>{quantities.length > 0 ? quantities.join(" · ") : "as needed"}</span>
    </li>
  );
}
