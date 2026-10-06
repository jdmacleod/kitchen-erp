import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isPositiveDecimal, useUnits, useUpdateProduct, type Product } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { inboxKey } from "../../api/inbox";
import { priceKeys } from "../../api/pricebook";
import { piecesFit } from "../../pages/catalog/ProductForm";
import { UnitSelect } from "../catalog/UnitSelect";
import { Alert, Button, Field } from "../ui";

interface QuickPackEditorProps {
  productId: string;
  /** "Bulk flour", for the field labels' context and the saved notice. */
  title: string;
  onSaved: (product: Product) => void;
  onCancel: () => void;
}

/**
 * Set a product's pack in place on Needs a bridge (issue 186): quantity, unit and,
 * for a pack sold by weight or volume, how many pieces it holds. Saving goes
 * through the ordinary product update, which re-prices the product's
 * observations; the row then leaves the list once its prices compare.
 */
export function QuickPackEditor({ productId, title, onSaved, onCancel }: QuickPackEditorProps) {
  const client = useQueryClient();
  const units = useUnits();
  const update = useUpdateProduct(productId);
  const [qty, setQty] = useState("");
  const [unit, setUnit] = useState("");
  const [pieces, setPieces] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const showPieces = piecesFit(unit, units.data ?? []);
  const prefix = `pack-${productId}`;

  function validate(): string | null {
    if (!isPositiveDecimal(qty)) return "Pack quantity must be a positive number, like 500.";
    if (!unit) return "Choose a pack unit.";
    if (showPieces && pieces.trim() && !/^[1-9]\d{0,4}$/.test(pieces.trim())) {
      return "Pieces must be a whole number, like 5.";
    }
    return null;
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const problem = validate();
    setInvalid(problem);
    if (problem) return;
    const count = showPieces && pieces.trim() ? Number(pieces.trim()) : undefined;
    update.mutate(
      { pack_qty: qty.trim(), pack_unit: unit, ...(count ? { pack_count: count } : {}) },
      {
        onSuccess: (product) => {
          void client.invalidateQueries({ queryKey: priceKeys.needsBridge });
          void client.invalidateQueries({ queryKey: inboxKey });
          onSaved(product);
        },
      },
    );
  }

  return (
    <form
      onSubmit={submit}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
      aria-label={`Pack for ${title}`}
      className="flex flex-col gap-3 py-2"
      noValidate
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Field
          id={`${prefix}-qty`}
          label="Pack quantity"
          inputMode="decimal"
          autoComplete="off"
          autoFocus
          value={qty}
          onChange={(e) => setQty(e.target.value)}
        />
        <UnitSelect id={`${prefix}-unit`} label="Pack unit" value={unit} onChange={setUnit} emptyLabel="Choose…" />
        {showPieces ? (
          <Field
            id={`${prefix}-pieces`}
            label="Pieces (optional)"
            inputMode="numeric"
            autoComplete="off"
            value={pieces}
            onChange={(e) => setPieces(e.target.value)}
            hint="How many it holds, like 5 links."
          />
        ) : null}
      </div>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {update.isError ? <Alert tone="error">{errorMessage(update.error)}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save pack"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
