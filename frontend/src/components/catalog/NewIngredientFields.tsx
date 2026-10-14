import { useState } from "react";
import { useIngredientSearch } from "../../api/catalog";
import { useConformedName, type NewIngredientInput } from "../../api/newIngredient";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { CategoryChip } from "../CategoryChip";
import { Button, Field } from "../ui";
import { CategoryField } from "./CategoryField";
import { Badge } from "./fields";
import { suggestedHint, useSuggestedCategory } from "./NewIngredientCategory";
import { NamePreview } from "./NewIngredientName";

interface Standard {
  key: string;
  name: string;
}

function problemOf(name: string, standard: Standard | null, category: string): string | null {
  if (!name) return "A name is required.";
  if (!standard && !category) return "Choose a category: every ingredient has one.";
  return null;
}

function inputOf(
  name: string,
  standard: Standard | null,
  category: string,
  kept: boolean,
): NewIngredientInput {
  if (standard) return { name: standard.name, standard_key: standard.key };
  return kept ? { name, category, keep_name: true } : { name, category };
}

/**
 * The name and category of an ingredient being created from a form (issue 344, issue 345):
 * the typed name, what it will be saved as, a standard-list entry it may be,
 * and the category, prefilled. ``problem`` says what still stops the save.
 */
export function useNewIngredientDraft(initialName = "") {
  const [typed, setTyped] = useState(initialName);
  const [kept, setKept] = useState(false);
  const [standard, setStandard] = useState<Standard | null>(null);
  const [chosen, setChosen] = useState("");
  const [touched, setTouched] = useState(false);
  const debounced = useDebouncedValue(typed.trim(), 300);
  const preview = useConformedName(standard ? "" : debounced);
  const conformed = preview.data?.name ?? null;
  const name = standard?.name ?? (kept || !conformed ? typed.trim() : conformed);
  const suggested = useSuggestedCategory(standard ? "" : name, chosen, touched);
  const category = suggested.category.trim();
  return {
    typed,
    name,
    debounced,
    standard,
    kept,
    conformed,
    changes: preview.data?.changes ?? [],
    category: suggested.category,
    source: suggested.source,
    problem: problemOf(name, standard, category),
    input: () => inputOf(name, standard, category, kept),
    dirty: typed !== initialName || chosen !== "" || standard !== null,
    type: (text: string) => {
      setTyped(text);
      setStandard(null);
    },
    keep: setKept,
    useStandard: setStandard,
    choose: (category: string) => {
      setTouched(true);
      setChosen(category);
    },
  };
}

export type NewIngredientDraft = ReturnType<typeof useNewIngredientDraft>;

/** The fields for a draft: name with its preview and standard offers, then category. */
export function NewIngredientFields({ draft }: { draft: NewIngredientDraft }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-1">
        <Field
          id="new-ingredient-name"
          label="Name"
          autoComplete="off"
          required
          value={draft.typed}
          onChange={(e) => draft.type(e.target.value)}
        />
        {draft.standard ? (
          <p className="flex flex-wrap items-center gap-2 text-xs">
            <Badge>from the standard list</Badge>
            <Button
              variant="ghost"
              className="min-h-11 lg:min-h-8 px-2 text-xs"
              onClick={() => draft.useStandard(null)}
            >
              Don't use it
            </Button>
          </p>
        ) : (
          <>
            <NamePreview
              id="new-ingredient-name"
              typed={draft.typed.trim()}
              conformed={draft.conformed}
              changes={draft.changes}
              kept={draft.kept}
              onKeep={draft.keep}
            />
            <StandardOffers text={draft.debounced} onUse={draft.useStandard} />
          </>
        )}
      </div>
      {draft.standard ? null : (
        <CategoryField
          id="new-ingredient-category"
          value={draft.category}
          required
          onChange={draft.choose}
          hint={suggestedHint(draft.source)}
        />
      )}
    </div>
  );
}

/** Standard-list entries the typed name may mean, to create from instead (issue 345). */
function StandardOffers({ text, onUse }: { text: string; onUse: (s: Standard) => void }) {
  const search = useIngredientSearch(text, true, 5);
  const offers = (search.data ?? []).filter((m) => m.kind === "standard").slice(0, 3);
  if (offers.length === 0) return null;
  return (
    <div
      role="group"
      aria-label="From the standard list"
      className="flex flex-wrap items-center gap-1 text-xs"
    >
      <span className="text-neutral-600 dark:text-neutral-400">From the standard list:</span>
      {offers.map((m) => (
        <Button
          key={m.key}
          variant="secondary"
          className="min-h-11 lg:min-h-8 px-2 text-xs"
          onClick={() => onUse({ key: m.key ?? "", name: m.name })}
        >
          {m.name} <CategoryChip category={m.category} categoryKey={m.category_key} />
        </Button>
      ))}
    </div>
  );
}
