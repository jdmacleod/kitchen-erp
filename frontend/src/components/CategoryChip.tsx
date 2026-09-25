/**
 * The ingredient category chip (docs/spec/08-ui-design-system.md, D12).
 *
 * The backend owns category normalization (backend/app/catalog/categories.py) and
 * sends each ingredient's `category_key` beside its free-text `category`. This
 * renders that key's colour with the text as the label. There is deliberately no
 * synonym map here: a category the backend does not recognize has a null key and
 * gets the neutral chip.
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

/** Class names for any element that should carry a category colour, e.g. "cat cat-dairy". */
export function categoryClass(key: CategoryKey | null | undefined): string {
  return key ? `cat cat-${key}` : "cat";
}

interface Props {
  /** The ingredient's free-text category, shown as the label. */
  category: string | null | undefined;
  categoryKey: CategoryKey | null | undefined;
  className?: string;
}

/** A tinted pill with a coloured dot. Renders nothing for an ingredient without a category. */
export function CategoryChip({ category, categoryKey, className = "" }: Props) {
  const label = category?.trim();
  if (!label) return null;
  return <span className={`${categoryClass(categoryKey)} cat-chip ${className}`.trim()}>{label}</span>;
}
