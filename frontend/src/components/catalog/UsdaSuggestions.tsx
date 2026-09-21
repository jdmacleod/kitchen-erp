import { trimDecimal, useUsdaSuggestions, type UsdaSuggestion } from "../../api/catalog";
import { Button } from "../ui";

export interface QueuedMeasure {
  label: string;
  canonical_qty: string;
  from_portion: string;
}

interface UsdaSuggestionsProps {
  /** The (debounced) ingredient name being typed. */
  name: string;
  /** Measures are in grams; only offer them when the ingredient's unit is g. */
  canonicalUnit: string;
  onUseDensity: (density_g_per_ml: string, from_portion: string) => void;
  onAddMeasure: (measure: QueuedMeasure) => void;
  queued: QueuedMeasure[];
}

/**
 * Compact suggestions from the optional USDA reference table. Renders nothing
 * at all when the table is not loaded or nothing matches: the form must feel
 * identical without it.
 */
export function UsdaSuggestions({ name, canonicalUnit, onUseDensity, onAddMeasure, queued }: UsdaSuggestionsProps) {
  const suggestions = useUsdaSuggestions(name);
  if (!suggestions.data || !suggestions.data.loaded || suggestions.data.items.length === 0) return null;

  return (
    <section
      aria-labelledby="usda-suggestions-heading"
      className="rounded-md border border-blue-200 bg-blue-50 p-3 dark:border-blue-900 dark:bg-blue-950/40"
    >
      <h3 id="usda-suggestions-heading" className="text-sm font-medium">
        Reference suggestions
      </h3>
      <p className="mt-0.5 text-xs text-neutral-600 dark:text-neutral-400">
        From the USDA table. Anything you accept is recorded as unconfirmed until you confirm it.
      </p>
      <ul className="mt-2 flex flex-col gap-2">
        {suggestions.data.items.map((item) => (
          <SuggestionRow
            key={item.fdc_id}
            item={item}
            canonicalUnit={canonicalUnit}
            onUseDensity={onUseDensity}
            onAddMeasure={onAddMeasure}
            queued={queued}
          />
        ))}
      </ul>
    </section>
  );
}

function SuggestionRow({
  item,
  canonicalUnit,
  onUseDensity,
  onAddMeasure,
  queued,
}: { item: UsdaSuggestion } & Omit<UsdaSuggestionsProps, "name">) {
  const density = item.densities[0];
  return (
    <li className="text-sm">
      <p className="font-medium">{item.description}</p>
      <div className="mt-1 flex flex-wrap gap-2">
        {density ? (
          <Button
            variant="secondary"
            className="min-h-8 px-2 text-xs"
            onClick={() => onUseDensity(density.density_g_per_ml, density.from_portion)}
          >
            Use density {trimDecimal(density.density_g_per_ml)} g/ml (from {density.from_portion})
          </Button>
        ) : null}
        {canonicalUnit === "g"
          ? item.measures.map((m) => {
              const already = queued.some((q) => q.label.toLowerCase() === m.label.toLowerCase());
              return (
                <Button
                  key={m.label}
                  variant="secondary"
                  className="min-h-8 px-2 text-xs"
                  disabled={already}
                  onClick={() =>
                    onAddMeasure({ label: m.label, canonical_qty: m.canonical_qty_g, from_portion: m.from_portion })
                  }
                >
                  {already ? "Queued: " : "Add measure "}
                  {m.label} = {trimDecimal(m.canonical_qty_g)} g
                </Button>
              );
            })
          : null}
      </div>
    </li>
  );
}
