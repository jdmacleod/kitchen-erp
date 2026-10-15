import { useEffect, useState } from "react";
import { useCategorySuggestion, type CategorySource } from "../../api/newIngredient";
import { CATEGORY_KEYS, CategoryChip, type CategoryKey } from "../CategoryChip";
import { Button } from "../ui";
import { CategoryField } from "./CategoryField";
import { labelClass } from "./fields";

const FROM: Record<CategorySource, string> = {
  name: "from its name",
  standard: "from the standard list",
  usda: "from USDA",
  clip: "from the page",
  catalog: "like similar ingredients",
};

/** "Suggested from USDA.", for a field prefilled with a suggestion. */
export function suggestedHint(source: CategorySource | null): string | undefined {
  return source ? `Suggested ${FROM[source]}.` : undefined;
}

/**
 * A form's category for a new ingredient: what the person chose, or until they
 * choose, the likeliest one for the name typed (issue 344).
 */
export function useSuggestedCategory(name: string, chosen: string, touched: boolean) {
  const suggestion = useCategorySuggestion(name);
  const suggested = touched || chosen ? null : (suggestion.data ?? null);
  return { category: chosen || (suggested?.category ?? ""), source: suggested?.source ?? null };
}

const keyOf = (value: string): CategoryKey | null =>
  (CATEGORY_KEYS as readonly string[]).includes(value) ? (value as CategoryKey) : null;

/**
 * The category of an ingredient about to be created (issue 344). Every new ingredient
 * has one, so it is asked for here, prefilled with the likeliest category and
 * shown as a chip: confirming it costs nothing, changing it is one click.
 */
export function NewIngredientCategory({
  id,
  name,
  value,
  source,
  onChange,
  disabled,
  proposalId,
}: {
  id: string;
  name: string;
  value: string;
  source?: CategorySource | null;
  onChange: (category: string, source: CategorySource | null) => void;
  disabled?: boolean;
  /** A product proposal whose captured category may say where this belongs. */
  proposalId?: string;
}) {
  const suggestion = useCategorySuggestion(name, { proposalId });
  const [editing, setEditing] = useState(false);
  const suggested = suggestion.data?.category ?? null;
  const suggestedFrom = suggestion.data?.source ?? null;
  // Prefill once the suggestion answers, unless a category is already chosen.
  useEffect(() => {
    if (!value && suggested) onChange(suggested, suggestedFrom);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the suggestion changes
  }, [suggested, suggestedFrom]);

  if (value && !editing) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm" data-testid={`${id}-chip`}>
        <span className={labelClass}>Category</span>
        <CategoryChip category={value} categoryKey={keyOf(value)} />
        {source ? (
          <span className="text-xs text-neutral-600 dark:text-neutral-400">{FROM[source]}</span>
        ) : null}
        <Button
          variant="ghost"
          className="min-h-11 lg:min-h-8 px-2"
          disabled={disabled}
          onClick={() => setEditing(true)}
          aria-label={`Change the category of ${name}`}
        >
          Change
        </Button>
      </div>
    );
  }
  return (
    <CategoryField
      id={id}
      value={value}
      required
      disabled={disabled}
      onChange={(category) => onChange(category, null)}
      hint={
        suggestion.isLoading
          ? "Finding a likely category…"
          : `Every ingredient has one: it sets the label and keep times for ${name}.`
      }
    />
  );
}
