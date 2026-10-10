import type { RecipeSummary } from "../../api/recipes";
import type { RecipeSearchBadge } from "../../api/search";
import { Badge } from "../catalog/fields";

/**
 * A recipe's badges, always in words (UI-7.4, UI-7.19): "Uncommitted" (neutral)
 * on a dirty file, "Can't read" (tomato) on a parse error, "Missing" (squash) on
 * a file no longer there. The same words on the list, the recipe page and search.
 */
export function RecipeBadge({ badge }: { badge: RecipeSearchBadge }) {
  if (badge === "parse_error") return <Badge tone="danger">Can&apos;t read</Badge>;
  if (badge === "missing") return <Badge tone="warn">Missing</Badge>;
  return <Badge>Uncommitted</Badge>;
}

/** The badges a recipe carries from its status and dirtiness, or nothing. */
export function RecipeBadges({ recipe }: { recipe: Pick<RecipeSummary, "status" | "dirty"> }) {
  const badges: RecipeSearchBadge[] = [];
  if (recipe.status === "parse_error") badges.push("parse_error");
  if (recipe.status === "missing") badges.push("missing");
  if (recipe.dirty && recipe.status !== "missing") badges.push("uncommitted");
  if (badges.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {badges.map((b) => (
        <RecipeBadge key={b} badge={b} />
      ))}
    </span>
  );
}
