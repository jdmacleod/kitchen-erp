import { useEffect } from "react";
import { useConformedName } from "../../api/newIngredient";
import { Button } from "../ui";

/**
 * "Will be saved as “frozen okra”" under a typed ingredient name (issue 345): the
 * name conformed to the 1G rules (lowercase but for proper nouns, culinary word
 * order, no brand in front), why, and a button to keep the name as typed.
 */
export function NamePreview({
  id,
  typed,
  conformed,
  changes,
  kept,
  onKeep,
  disabled,
}: {
  id: string;
  typed: string;
  conformed: string | null;
  changes: string[];
  kept: boolean;
  onKeep: (keep: boolean) => void;
  disabled?: boolean;
}) {
  if (!conformed || conformed === typed) return null;
  if (kept) {
    return (
      <p className="flex flex-wrap items-center gap-2 text-xs" data-testid={`${id}-kept`}>
        <span className="text-neutral-600 dark:text-neutral-400">Saved as typed.</span>
        <Button
          variant="ghost"
          className="min-h-11 lg:min-h-8 px-2 text-xs"
          disabled={disabled}
          onClick={() => onKeep(false)}
        >
          Use “{conformed}”
        </Button>
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1 text-xs" data-testid={`${id}-preview`}>
      <p className="flex flex-wrap items-center gap-2">
        <span>
          Will be saved as <span className="font-medium">“{conformed}”</span>
        </span>
        <Button
          variant="ghost"
          className="min-h-11 lg:min-h-8 px-2 text-xs"
          disabled={disabled}
          onClick={() => onKeep(true)}
        >
          Keep “{typed}”
        </Button>
      </p>
      {changes.length > 0 ? (
        <p className="text-neutral-600 dark:text-neutral-400">{changes.join("; ")}.</p>
      ) : null}
    </div>
  );
}

/**
 * The preview for the ingredient picker's "Create new ingredient" choice: the
 * choice's name becomes the conformed one as soon as it answers, unless kept.
 */
export function NewIngredientName({
  id,
  typed,
  name,
  kept,
  onChange,
  disabled,
}: {
  id: string;
  /** What the person typed. */
  typed: string;
  /** The name that will be saved. */
  name: string;
  kept: boolean;
  onChange: (name: string, kept: boolean) => void;
  disabled?: boolean;
}) {
  const preview = useConformedName(typed);
  const conformed = preview.data?.name ?? null;
  useEffect(() => {
    if (!kept && conformed && conformed !== name) onChange(conformed, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the preview changes
  }, [conformed, kept]);
  return (
    <NamePreview
      id={id}
      typed={typed}
      conformed={conformed}
      changes={preview.data?.changes ?? []}
      kept={kept}
      disabled={disabled}
      onKeep={(keep) => onChange(keep ? typed : (conformed ?? typed), keep)}
    />
  );
}
