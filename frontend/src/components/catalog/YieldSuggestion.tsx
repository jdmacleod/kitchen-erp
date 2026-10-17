import { fractionToPercent, percentToFraction, trimDecimal } from "../../api/catalog";
import { useIngredientUsda } from "../../api/usdaReference";
import { Button } from "../ui";

interface YieldSuggestionProps {
  ingredientId: string;
  /** The yield field's current text, as a percentage. */
  value: string;
  /** Put the suggested percentage in the field; nothing is saved until the form is. */
  onUse: (percent: string) => void;
}

/**
 * The USDA refuse figure offered as the yield (07, 3D; issue 282). Renders
 * nothing when the ingredient has no USDA food or USDA gives no refuse: the
 * field must feel identical without it. Never fills the field by itself.
 */
export function YieldSuggestion({ ingredientId, value, onUse }: YieldSuggestionProps) {
  const usda = useIngredientUsda(ingredientId);
  const ref = usda.data?.reference;
  if (!ref?.refuse_pct || !ref.yield_pct) return null;
  const percent = fractionToPercent(ref.yield_pct);
  const same = percentToFraction(value) === trimDecimal(ref.yield_pct);
  return (
    <div className="flex flex-col items-start gap-1 text-xs text-neutral-600 dark:text-neutral-400">
      <p>
        USDA: {trimDecimal(ref.refuse_pct)}% refuse → yield {percent}% ({ref.description})
      </p>
      {same ? (
        <p>The yield matches it.</p>
      ) : (
        <Button
          variant="secondary"
          className="min-h-11 lg:min-h-8 px-2 text-xs"
          onClick={() => onUse(percent)}
        >
          Use {percent}%
        </Button>
      )}
    </div>
  );
}
