import { useCallback, useState, type ReactNode, type RefObject } from "react";
import type { Ingredient } from "../../api/catalog";
import { errorMessage, isApiError } from "../../api/client";
import { useDecideName, type ResolveDecision, type ResolveDecisionInput } from "../../api/recipes";
import { AddIngredientDrawer } from "../../pages/catalog/IngredientsPage";
import { IngredientPicker, type IngredientChoice } from "../catalog/IngredientPicker";
import { Alert, Button, focusRing } from "../ui";

/**
 * One recipe name's decision (07, 3C; UI-7.10, UI-7.16), shared by the resolve
 * page and the cost table: posts to `/recipes/resolve`, and turns the server's
 * refusals into words at the row. A name another ingredient already has comes
 * back `409 alias_taken` with the holder, shown as "'…' is already a spelling of
 * {holder}. Use {holder} · Choose another"; nothing is written until one is
 * pressed. `ingredient_inactive` shows its message. Any other failure shows an
 * alert and keeps the input.
 */
export interface Collision {
  holder: string;
  holderId: string;
}

export function useNameDecision({ nameNorm, rawName, onResolved }: { nameNorm: string; rawName: string; onResolved: (decision: ResolveDecision) => void }) {
  const mutation = useDecideName();
  const [collision, setCollision] = useState<Collision | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  // The picker's choice lives here so a refused decision can give the picker back
  // (10, "nothing is written until one is pressed").
  const [choice, setChoice] = useState<IngredientChoice | null>(null);

  const decide = useCallback(
    (input: Omit<ResolveDecisionInput, "name_norm">) => {
      setCollision(null);
      setFailure(null);
      mutation.mutate(
        { name_norm: nameNorm, ...input },
        {
          onSuccess: onResolved,
          onError: (e) => {
            setChoice(null);
            if (isApiError(e) && e.code === "alias_taken" && typeof e.details?.holder === "string" && typeof e.details?.holder_id === "string") {
              setCollision({ holder: e.details.holder, holderId: e.details.holder_id });
              return;
            }
            setFailure(errorMessage(e));
          },
        },
      );
    },
    [mutation, nameNorm, onResolved],
  );

  const messages: ReactNode = collision ? (
    <Alert tone="info">
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1" data-testid="alias-collision">
        <span>
          ‘{rawName}’ is already a spelling of <span className="font-medium">{collision.holder}</span>.
        </span>
        <Button variant="secondary" className="min-h-11 lg:min-h-8 px-2" disabled={mutation.isPending} onClick={() => decide({ ingredient_id: collision.holderId })}>
          Use {collision.holder}
        </Button>
        <span aria-hidden="true">·</span>
        <button type="button" className={`rounded font-medium underline ${focusRing}`} onClick={() => setCollision(null)}>
          Choose another
        </button>
      </span>
    </Alert>
  ) : failure ? (
    <Alert tone="error">{failure}</Alert>
  ) : null;

  return { decide, pending: mutation.isPending, collision, messages, choice, setChoice };
}

interface DecisionPickerProps {
  id: string;
  label?: string;
  hideLabel?: boolean;
  inputRef?: RefObject<HTMLInputElement | null>;
  autoFocus?: boolean;
  disabled?: boolean;
  /** The name as written, for the create drawer's prefilled name. */
  rawName: string;
  decide: (input: Omit<ResolveDecisionInput, "name_norm">) => void;
  pending: boolean;
  choice: IngredientChoice | null;
  setChoice: (choice: IngredientChoice | null) => void;
}

/**
 * The 1G ingredient picker as a decision: choosing an ingredient posts it at once
 * (Enter chooses, UI-7.12); a standard name creates one on choice; "Create new
 * ingredient" opens the catalog's create drawer with the name filled in, and
 * the new ingredient is then chosen.
 */
export function DecisionPicker({ id, label = "Choose an ingredient", hideLabel, inputRef, autoFocus, disabled, rawName, decide, pending, choice, setChoice }: DecisionPickerProps) {
  const [creating, setCreating] = useState<string | null>(null);

  const onChange = (next: IngredientChoice | null) => {
    setChoice(next);
    if (!next) return;
    if (next.kind === "existing") decide({ ingredient_id: next.ingredient.id });
    else if (next.kind === "standard") decide({ ingredient: { name: next.name, standard_key: next.key } });
    else setCreating(next.name);
  };

  const created = (ingredient: Ingredient) => {
    setCreating(null);
    setChoice({ kind: "existing", ingredient });
    decide({ ingredient_id: ingredient.id });
  };

  return (
    <>
      <IngredientPicker
        id={id}
        label={label}
        hideLabel={hideLabel}
        inputRef={inputRef}
        autoFocus={autoFocus}
        value={choice}
        onChange={onChange}
        disabled={disabled || pending}
        allowCreate
      />
      {creating !== null ? (
        <AddIngredientDrawer
          initialName={creating || rawName}
          onClose={() => {
            setCreating(null);
            setChoice(null);
          }}
          onCreated={created}
        />
      ) : null}
    </>
  );
}
