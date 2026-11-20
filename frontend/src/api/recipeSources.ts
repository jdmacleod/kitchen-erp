// What a recipe makes and which recipes cost from which (07, 3D; issue 526).
// The UI says "makes", never "yield": the ingredient page's "Yield (%)" is trim.
// `yield:` appears only as file syntax.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { formatMoney } from "../lib/decimal";
import { catalogKeys, type Ingredient } from "./catalog";
import { api } from "./client";
import { recipeKeys } from "./recipes";

export interface RecipeLink {
  id: string;
  title: string;
  path: string;
  status: "ok" | "parse_error" | "missing";
}

/** Front matter `yield:` as read; the amount and display unit only when it parses. */
export interface MakesOut {
  text: string | null;
  qty: string | null;
  unit: string | null;
  display_qty: string | null;
  display_unit: string | null;
}

/** Counted cost per display unit of what a recipe makes: "$0.62 per fl oz". */
export interface MakesUnitCost {
  amount: string;
  unit: string;
  partial: boolean;
  /** "latest" when a kitchen's view or this week's offers read the stored latest cost. */
  source_basis: string | null;
}

export interface MadeByRecipe extends RecipeLink {
  makes_text: string | null;
  makes_ok: boolean;
  unit_cost: MakesUnitCost | null;
}

export interface LinkWarning {
  code: "recipe_cycle";
  message: string;
  chain: RecipeLink[];
}

/** The file fix every recipe-side message ends with. */
export const YIELD_EXAMPLE = "yield: 500 ml";

/** "16.9 fl oz (500 ml)": the household's units first, the file's own text second. */
export function makesText(makes: MakesOut | null | undefined): string | null {
  if (!makes?.text) return null;
  if (!makes.display_qty || !makes.display_unit) return makes.text;
  const shown = `${makes.display_qty} ${makes.display_unit}`;
  return shown === makes.text ? shown : `${shown} (${makes.text})`;
}

/** Just the household's units: "16.9 fl oz", or the text as written. */
export function makesShort(makes: MakesOut | null | undefined): string | null {
  if (!makes?.text) return null;
  return makes.display_qty && makes.display_unit
    ? `${makes.display_qty} ${makes.display_unit}`
    : makes.text;
}

/** The amount as the server rounded it: 3 places below $1 ("$0.059"), else 2. */
export function unitAmount(cost: MakesUnitCost): string {
  return formatMoney(cost.amount, 2, 3);
}

/** "$0.059 per fl oz", "at least" when partial. */
export function unitCostText(cost: MakesUnitCost): string {
  const figure = `${unitAmount(cost)} per ${cost.unit}`;
  return cost.partial ? `at least ${figure}` : figure;
}

/** An ingredient as its detail read and its made-by PATCH answer (issue 526). */
export interface IngredientMadeBy extends Ingredient {
  /** The house recipe that makes it. */
  made_by_recipe?: MadeByRecipe | null;
  /** The one recipe titled like it, offered when none is linked and nothing has a price. */
  recipe_match?: RecipeLink | null;
  /** After a PATCH that set or cleared the link. */
  recomputed?: number | null;
  recompute_failed?: number | null;
  warnings?: LinkWarning[];
}

/**
 * Set or clear the recipe that makes an ingredient. The server commits the link,
 * then recomputes every recipe with a line of it, so recipe costs refresh too.
 */
export function useSetMadeBy(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (recipeId: string | null) =>
      api<IngredientMadeBy>(`/ingredients/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: { made_by_recipe_id: recipeId },
      }),
    onSuccess: (updated) => {
      client.setQueryData(catalogKeys.ingredient(id), updated);
      void client.invalidateQueries({ queryKey: recipeKeys.all });
    },
  });
}
