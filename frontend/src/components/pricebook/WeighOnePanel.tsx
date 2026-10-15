import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";
import { catalogKeys, type CanonicalUnit } from "../../api/catalog";
import { api, errorMessage } from "../../api/client";
import { Alert, Button, Field } from "../ui";

/**
 * "Weigh one" on a row whose pack is counted in pieces (issue 357). It saves the
 * ingredient's "each" measure, what one piece weighs or holds, once for the
 * ingredient. Every pack of N pieces under it is then priced through that
 * measure. Nothing is guessed: the person weighs one, or reads it from a label.
 */
export function WeighOnePanel({
  ingredientId,
  name,
  canonicalUnit,
  onDone,
  onCancel,
}: {
  ingredientId: string;
  name: string;
  canonicalUnit: CanonicalUnit;
  onDone: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const client = useQueryClient();
  const [qty, setQty] = useState("");
  const [tried, setTried] = useState(false);
  const valid = /^\d+(?:\.\d+)?$/.test(qty.trim()) && Number(qty) > 0;
  const save = useMutation({
    mutationFn: () =>
      api(`/ingredients/${encodeURIComponent(ingredientId)}/measures`, {
        method: "POST",
        body: { label: "each", canonical_qty: qty.trim(), source: "measured", confirmed: true },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["price-book"] });
      void client.invalidateQueries({ queryKey: catalogKeys.ingredient(ingredientId) });
      void client.invalidateQueries({ queryKey: ["inbox"] });
      onDone();
    },
  });
  const submit = (e: SyntheticEvent) => {
    e.preventDefault();
    setTried(true);
    if (valid) save.mutate();
  };
  const verb = canonicalUnit === "ml" ? "holds" : "weighs";

  return (
    <form
      onSubmit={submit}
      aria-label={`What one ${name} ${verb}`}
      className="flex flex-col gap-2 rounded-md border border-neutral-300 p-3 text-sm dark:border-neutral-700"
    >
      <p>
        Packs of {name} are counted in pieces. Say what one {verb}, and every pack of it is priced
        per {canonicalUnit}.
      </p>
      <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
        <Field
          id={`${id}-qty`}
          label={`One ${name} ${verb} (${canonicalUnit})`}
          hint="Weigh one, or read it from a label."
          inputMode="decimal"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-2 lg:flex">
          <Button type="submit" disabled={save.isPending}>
            Save
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
      {tried && !valid ? <Alert tone="error">Give a positive number, like 67.</Alert> : null}
      {save.isError ? <Alert tone="error">{errorMessage(save.error)}</Alert> : null}
    </form>
  );
}
