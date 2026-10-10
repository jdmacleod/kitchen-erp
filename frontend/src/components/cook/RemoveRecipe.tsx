import { useEffect, useId, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import { useDeleteRecipe, type Recipe } from "../../api/recipes";
import { useNavigateWithNotice } from "../Notice";
import { alertTones, Button, focusRing } from "../ui";

/**
 * "Remove recipe" for a missing recipe (10, Recipe page, Missing; UI-7.15):
 * confirmed in an inline tomato panel with focus on Cancel, never a browser
 * dialog. Afterwards the list opens with the Notice "Removed {title}."
 */
export function RemoveRecipe({ recipe }: { recipe: Recipe }) {
  const remove = useDeleteRecipe(recipe.id);
  const go = useNavigateWithNotice();
  const [confirming, setConfirming] = useState(false);
  const headingId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const alert = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (confirming) cancel.current?.focus();
  }, [confirming]);
  useEffect(() => {
    if (remove.isError) alert.current?.focus();
  }, [remove.isError, remove.failureCount]);

  const close = () => {
    setConfirming(false);
    remove.reset();
    requestAnimationFrame(() => trigger.current?.focus());
  };

  if (confirming) {
    return (
      <div role="group" aria-labelledby={headingId} className="flex flex-col gap-3 rounded-md border border-red-300 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950">
        <h3 id={headingId} className="text-sm font-medium">
          Remove ‘{recipe.title}’?
        </h3>
        <p className="text-sm">Its cost snapshots and pins go with it. The file itself is not touched.</p>
        {remove.isError ? (
          <div ref={alert} tabIndex={-1} role="alert" className={`rounded-md border px-3 py-2 text-sm ${alertTones.error} ${focusRing}`}>
            {errorMessage(remove.error)}
          </div>
        ) : null}
        <div className="flex flex-col gap-2 lg:flex-row">
          <Button
            variant="dangerFill"
            className="w-full lg:w-auto"
            disabled={remove.isPending}
            onClick={() =>
              remove.mutate(undefined, {
                onSuccess: () => go("/cook/recipes", { tone: "success", message: `Removed ${recipe.title}.` }),
              })
            }
          >
            {remove.isPending ? "Removing…" : "Remove recipe"}
          </Button>
          <Button ref={cancel} variant="secondary" className="w-full lg:w-auto" disabled={remove.isPending} onClick={close}>
            Cancel
          </Button>
        </div>
      </div>
    );
  }
  return (
    <Button
      ref={trigger}
      variant="danger"
      onClick={() => {
        remove.reset();
        setConfirming(true);
      }}
    >
      Remove recipe
    </Button>
  );
}
