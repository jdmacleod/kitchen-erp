import { useState } from "react";
import { Link } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import { useIngredient, type Ingredient, type IngredientSummary } from "../../api/catalog";
import { useLinkAction, type MergeNeeded, type StandardEntry } from "../../api/ingredientLink";
import { useNotice } from "../Notice";
import { Alert, Button, Card, focusRing } from "../ui";
import { ChooseStandard, IngredientMergePanel } from "./IngredientMergePanel";
import { IngredientPicker, type IngredientChoice } from "./IngredientPicker";

/**
 * "Merge into…" and "Link to standard name" on an ingredient's own page
 * (issue 211; 03 and 10, 1G). Both reuse the link page's merge, preview and
 * standard-list search, so the page and the link list can never disagree.
 */
export type IngredientPageMode = "idle" | "merge" | "link";

export function IngredientPageActions({ ingredient, mode, onClose }: { ingredient: Ingredient; mode: IngredientPageMode; onClose: () => void }) {
  if (mode === "merge") return <MergeInto ingredient={ingredient} onClose={onClose} />;
  if (mode === "link") return <LinkToStandard ingredient={ingredient} onClose={onClose} />;
  return null;
}

/** A merged ingredient's page says where it went; its old links still land here. */
export function MergedInto({ survivorId }: { survivorId: string }) {
  const survivor = useIngredient(survivorId);
  const name = survivor.data?.name ?? "another ingredient";
  return (
    <Alert tone="info">
      Merged into{" "}
      <Link to={`/catalog/ingredients/${survivorId}`} className={`rounded font-medium underline ${focusRing}`}>
        {name}
      </Link>
      . Its products and spellings are there now.
    </Alert>
  );
}

function useMergedNotice(ingredient: Ingredient, onClose: () => void) {
  const notice = useNotice();
  return ({ survivor, loser }: { survivor: { id: string; name: string }; loser: { name: string }; targetName: string }) => {
    onClose();
    if (survivor.id === ingredient.id) {
      notice.show({ tone: "success", message: `Merged ${loser.name} into ${ingredient.name}.` });
      return;
    }
    notice.show({
      tone: "success",
      message: `Merged into ${survivor.name}.`,
      action: { label: `Open ${survivor.name}`, to: `/catalog/ingredients/${survivor.id}` },
      focusAction: true,
    });
  };
}

function MergeInto({ ingredient, onClose }: { ingredient: Ingredient; onClose: () => void }) {
  const [choice, setChoice] = useState<IngredientChoice | null>(null);
  const merged = useMergedNotice(ingredient, onClose);
  const other: IngredientSummary | null = choice?.kind === "existing" ? choice.ingredient : null;
  const self = other?.id === ingredient.id;

  return (
    <Card>
      <h2 className="mb-3 text-lg font-medium">Merge {ingredient.name} into another ingredient</h2>
      {other && !self ? (
        <IngredientMergePanel
          first={{ id: ingredient.id, name: ingredient.name }}
          second={{ id: other.id, name: other.name }}
          defaultSurvivor={other.id}
          // The kept ingredient keeps its own name; the other becomes a spelling of it.
          targetFor={(survivor) => ({ target: { name: survivor.name }, name: survivor.name })}
          onCancel={() => setChoice(null)}
          onMerged={merged}
        />
      ) : (
        <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
          <div className="lg:w-80">
            <IngredientPicker id={`merge-${ingredient.id}-into`} label="Merge into" value={null} onChange={setChoice} allowCreate={false} />
          </div>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        </div>
      )}
      {self ? (
        <Alert tone="error" className="mt-2">
          Choose another ingredient. This is the one you&apos;re on.
        </Alert>
      ) : null}
    </Card>
  );
}

function LinkToStandard({ ingredient, onClose }: { ingredient: Ingredient; onClose: () => void }) {
  const action = useLinkAction();
  const notice = useNotice();
  const merged = useMergedNotice(ingredient, onClose);
  const [needed, setNeeded] = useState<{ entry: StandardEntry; needed: MergeNeeded } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const link = (entry: StandardEntry) => {
    setError(null);
    action.mutate(
      { id: ingredient.id, action: "link", standard_key: entry.key },
      {
        onSuccess: () => {
          onClose();
          notice.show({ tone: "success", message: `Linked ${ingredient.name} to ${entry.name}.` });
        },
        onError: (e) => {
          // Another ingredient already has the standard name: linking merges the two.
          if (isApiError(e) && e.code === "merge_needed" && e.details) {
            setNeeded({ entry, needed: e.details as unknown as MergeNeeded });
            return;
          }
          setError(errorMessage(e));
        },
      },
    );
  };

  return (
    <Card>
      <h2 className="mb-3 text-lg font-medium">Link {ingredient.name} to a standard name</h2>
      {needed ? (
        <IngredientMergePanel
          first={{ id: ingredient.id, name: ingredient.name }}
          second={{ id: needed.needed.other.id, name: needed.needed.other.name, products: needed.needed.other.products }}
          defaultSurvivor={needed.needed.other.id}
          targetFor={() => ({ target: { standard_key: needed.entry.key }, name: needed.needed.target_name })}
          onCancel={() => setNeeded(null)}
          onMerged={merged}
        />
      ) : (
        <ChooseStandard id={`link-${ingredient.id}-standard`} onChosen={link} onCancel={onClose} />
      )}
      {action.isPending ? (
        <p role="status" className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">
          Linking…
        </p>
      ) : null}
      {error ? (
        <Alert tone="error" className="mt-2">
          {error}
        </Alert>
      ) : null}
    </Card>
  );
}
