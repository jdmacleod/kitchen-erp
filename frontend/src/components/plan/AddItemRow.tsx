import { useEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { productTitle, type IngredientSummary, type SearchHit } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useAddItem, type ItemCreateInput, type ShoppingItem } from "../../api/shoppingList";
import { IngredientPicker, type IngredientChoice } from "../catalog/IngredientPicker";
import { ProductTypeahead } from "../catalog/ProductTypeahead";
import { Alert, Button, Field } from "../ui";
import { AmountRows, type AmountRow } from "./AmountRows";
import { amountsInput, halfFilledAmount } from "./amounts";
import { ItemFlags } from "./ItemFlags";

type Target = { kind: "ingredient"; id: string; ingredient: IngredientSummary } | { kind: "product"; id: string; name: string };

interface AddItemRowProps {
  homeBaseId: string;
  /** Name only: the Home panel's one-field add (UI-8.9). */
  compact?: boolean;
  /** The picker's input, so "Add item" can put focus there. */
  inputRef?: RefObject<HTMLInputElement | null>;
  autoFocus?: boolean;
  onAdded: (item: ShoppingItem) => void;
}

const EMPTY: AmountRow[] = [{ amount: "", unit: "" }];

/**
 * The inline add row (10, Plan: Inline add; UI-8.3): the 1G ingredient picker
 * labelled "What do you need?", a "Use product…" switch to the product search
 * for a product item, an optional amount and unit, "Deferrable" and "Minimum
 * quality" chips, a note behind "Add a note", and "Add". Enter adds and keeps
 * focus in the picker, so a list can be typed through.
 */
export function AddItemRow({ homeBaseId, compact = false, inputRef, autoFocus, onAdded }: AddItemRowProps) {
  const add = useAddItem();
  const ownRef = useRef<HTMLInputElement | null>(null);
  const ref = inputRef ?? ownRef;
  const [byProduct, setByProduct] = useState(false);
  const [target, setTarget] = useState<Target | null>(null);
  const [amounts, setAmounts] = useState<AmountRow[]>(EMPTY);
  const [deferrable, setDeferrable] = useState(false);
  const [minQuality, setMinQuality] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const [focusTick, setFocusTick] = useState(0);
  const formId = compact ? "add-list-item-compact" : "add-list-item";

  // After an add the picker remounts empty; focus goes back into it once it has,
  // so the next item can be typed at once (UI-8.3).
  useEffect(() => {
    if (focusTick > 0) ref.current?.focus();
  }, [focusTick, ref]);

  const reset = () => {
    setTarget(null);
    setAmounts(EMPTY);
    setDeferrable(false);
    setMinQuality("");
    setNote("");
    setInvalid(null);
    setFocusTick((t) => t + 1);
  };

  const submit = (target: Target | null) => {
    if (!target) {
      setInvalid(byProduct ? "Choose a product." : "Choose an ingredient.");
      return;
    }
    const problem = compact ? null : halfFilledAmount(amounts);
    if (problem) {
      setInvalid(problem);
      return;
    }
    setInvalid(null);
    const trimmedNote = note.trim();
    const input: ItemCreateInput = {
      home_base_id: homeBaseId,
      kind: target.kind,
      ...(target.kind === "product" ? { product_id: target.id } : { ingredient_id: target.id }),
      amounts: compact ? [] : amountsInput(amounts),
      deferrable: compact ? false : deferrable,
      note: !compact && trimmedNote ? trimmedNote : null,
      ...(!compact && target.kind === "ingredient" && minQuality ? { min_quality: Number(minQuality) } : {}),
      added_from: "manual",
    };
    add.mutate(input, {
      onSuccess: (item) => {
        reset();
        onAdded(item);
      },
    });
  };
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit(target);
  };

  // Choosing a name is the whole entry in the one-field form, so it adds at once;
  // in the full row, focus moves on to the amount so Enter there adds.
  const chosen = (next: Target) => {
    setTarget(next);
    if (compact) submit(next);
    else queueMicrotask(() => document.getElementById(`${formId}-amount-0`)?.focus());
  };
  const onIngredient = (choice: IngredientChoice | null) => {
    if (!choice) return setTarget(null);
    if (choice.kind !== "existing") return;
    chosen({ kind: "ingredient", id: choice.ingredient.id, ingredient: choice.ingredient });
  };
  const onProduct = (hit: SearchHit) => chosen({ kind: "product", id: hit.id, name: productTitle(hit) });

  const pickerValue: IngredientChoice | null = target?.kind === "ingredient" ? { kind: "existing", ingredient: target.ingredient } : null;

  return (
    <form id={formId} onSubmit={onSubmit} noValidate aria-label="Add an item" className="flex flex-col gap-2" data-testid={formId}>
      <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
        <div className="min-w-0 flex-1">
          {byProduct ? (
            target?.kind === "product" ? (
              <div className="flex flex-col gap-1">
                <span className="text-sm font-medium">Product</span>
                <div className="flex min-h-11 flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-300 bg-neutral-50 px-3 py-1 text-sm lg:min-h-10 dark:border-neutral-700 dark:bg-neutral-900">
                  <span className="font-medium" data-testid="add-product-choice">
                    {target.name}
                  </span>
                  <Button variant="ghost" className="min-h-11 px-2 lg:min-h-8" onClick={() => setTarget(null)}>
                    Change
                  </Button>
                </div>
              </div>
            ) : (
              <ProductTypeahead id={`${formId}-product`} label="Which product?" inputRef={ref} autoFocus={autoFocus} onSelect={onProduct} />
            )
          ) : (
            <IngredientPicker id={`${formId}-ingredient`} label="What do you need?" value={pickerValue} onChange={onIngredient} allowCreate={false} inputRef={ref} autoFocus={autoFocus} />
          )}
        </div>
        {compact ? null : (
          <Button
            variant="ghost"
            className="self-start px-2 lg:self-end"
            aria-pressed={byProduct}
            onClick={() => {
              setByProduct((v) => !v);
              setTarget(null);
              setMinQuality("");
            }}
          >
            {byProduct ? "Use ingredient…" : "Use product…"}
          </Button>
        )}
        <Button type="submit" disabled={add.isPending} className="self-start lg:self-end">
          {add.isPending ? "Adding…" : "Add"}
        </Button>
      </div>
      {compact ? null : (
        <>
          <div className="flex flex-col gap-2 lg:flex-row lg:flex-wrap lg:items-end lg:gap-4">
            <AmountRows idPrefix={formId} rows={amounts} onChange={setAmounts} />
            <ItemFlags idPrefix={formId} kind={target?.kind ?? (byProduct ? "product" : "ingredient")} deferrable={deferrable} minQuality={minQuality} onDeferrable={setDeferrable} onMinQuality={setMinQuality} />
          </div>
          {noteOpen ? (
            <Field id={`${formId}-note`} label="Note" value={note} maxLength={500} autoComplete="off" onChange={(e) => setNote(e.target.value)} />
          ) : (
            <div>
              <Button variant="ghost" className="px-2" onClick={() => setNoteOpen(true)}>
                Add a note
              </Button>
            </div>
          )}
        </>
      )}
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {add.isError ? <Alert tone="error">Couldn&apos;t add it: {errorMessage(add.error)}</Alert> : null}
    </form>
  );
}
