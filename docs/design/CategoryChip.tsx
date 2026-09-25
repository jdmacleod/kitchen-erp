/**
 * Category chip for ingredient categories.
 *
 * Ingredient.category is free text and nullable, so this normalizes common
 * spellings onto a fixed set of color keys. Unknown or empty categories get
 * the neutral chip, so nothing breaks as new categories appear.
 */

export const CATEGORY_KEYS = [
  "produce",
  "dairy",
  "meat",
  "seafood",
  "bakery",
  "pantry",
  "spices",
  "beverages",
  "frozen",
] as const;

export type CategoryKey = (typeof CATEGORY_KEYS)[number];

const SYNONYMS: Record<string, CategoryKey> = {
  produce: "produce", vegetable: "produce", vegetables: "produce", veg: "produce",
  fruit: "produce", fruits: "produce", herbs: "produce", greens: "produce",
  dairy: "dairy", cheese: "dairy", eggs: "dairy", milk: "dairy",
  meat: "meat", poultry: "meat", beef: "meat", pork: "meat", charcuterie: "meat",
  seafood: "seafood", fish: "seafood", shellfish: "seafood",
  bakery: "bakery", bread: "bakery",
  pantry: "pantry", "dry goods": "pantry", grains: "pantry", canned: "pantry",
  baking: "pantry", oils: "pantry", condiments: "pantry",
  spices: "spices", spice: "spices", seasoning: "spices", seasonings: "spices",
  beverages: "beverages", beverage: "beverages", drinks: "beverages",
  coffee: "beverages", tea: "beverages",
  frozen: "frozen",
};

export function categoryKey(category: string | null | undefined): CategoryKey | null {
  if (!category) return null;
  return SYNONYMS[category.trim().toLowerCase()] ?? null;
}

/** Class names for any element that should carry a category color (e.g. "cat cat-dairy"). */
export function categoryClass(category: string | null | undefined): string {
  const key = categoryKey(category);
  return key ? `cat cat-${key}` : "cat";
}

type Props = {
  category: string | null | undefined;
  /** Text shown when the ingredient has no category. Pass null to render nothing. */
  emptyLabel?: string | null;
  className?: string;
};

export function CategoryChip({ category, emptyLabel = "Uncategorized", className = "" }: Props) {
  const label = category?.trim() || emptyLabel;
  if (!label) return null;
  return (
    <span className={`${categoryClass(category)} cat-chip ${className}`.trim()}>
      {label}
    </span>
  );
}
