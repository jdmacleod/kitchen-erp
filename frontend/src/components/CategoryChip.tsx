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

/**
 * A row's category as the filter chips name it (issue 247): the group from the
 * backend's category_key, with the ingredient's own wording beside it when that
 * says something more ("Pantry · Dry goods"). Without a group, the wording alone.
 */
export function ProductCategory({ category, categoryKey }: { category: string | null; categoryKey: CategoryKey | null }) {
  const own = category?.trim() || null;
  if (!categoryKey) return <CategoryChip category={own} categoryKey={null} />;
  const group = categoryKey[0].toUpperCase() + categoryKey.slice(1);
  const extra = own && own.toLowerCase() !== categoryKey ? own : null;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5">
      <CategoryChip category={group} categoryKey={categoryKey} />
      {extra ? <span className="text-sm text-neutral-600 dark:text-neutral-400">{extra}</span> : null}
    </span>
  );
}
