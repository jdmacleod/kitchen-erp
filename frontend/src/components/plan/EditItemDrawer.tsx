import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { errorMessage } from "../../api/client";
import { itemName, staleItem, usePatchItem, type ShoppingItem, type ShoppingList, type listKeys } from "../../api/shoppingList";
import { Drawer } from "../Drawer";
import { Alert, Field } from "../ui";
import { AmountRows, type AmountRow } from "./AmountRows";
import { amountsInput, halfFilledAmount } from "./amounts";
import { ItemFlags } from "./ItemFlags";

interface EditItemDrawerProps {
  item: ShoppingItem;
  listKey: ReturnType<typeof listKeys.list>;
  onClose: () => void;
  onNotice: (message: string) => void;
}

interface Form {
  amounts: AmountRow[];
  note: string;
  deferrable: boolean;
  minQuality: string;
}

function initial(item: ShoppingItem): Form {
  return {
    amounts: item.amounts.length > 0 ? item.amounts.map((a) => ({ amount: a.amount, unit: a.unit })) : [{ amount: "", unit: "" }],
    note: item.note ?? "",
    deferrable: item.deferrable,
    minQuality: item.min_quality === null ? "" : String(item.min_quality),
  };
}

/**
 * Edit a row's amounts, note, deferrable and minimum quality (10, Plan: Edit).
 * The PATCH carries the `updated_at` the row was loaded with; a 409 `stale_item`
 * puts the server's row in place of the one shown and says so (UI-8.6).
 */
export function EditItemDrawer({ item, listKey, onClose, onNotice }: EditItemDrawerProps) {
  const client = useQueryClient();
  const patch = usePatchItem();
  const [form, setForm] = useState<Form>(() => initial(item));
  const [invalid, setInvalid] = useState<string | null>(null);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial(item));
  const name = itemName(item);
  const formId = `edit-item-${item.id}`;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const problem = halfFilledAmount(form.amounts);
    if (problem) {
      setInvalid(problem);
      return;
    }
    setInvalid(null);
    const note = form.note.trim();
    patch.mutate(
      {
        id: item.id,
        input: {
          updated_at: item.updated_at,
          amounts: amountsInput(form.amounts),
          note: note ? note : null,
          deferrable: form.deferrable,
          ...(item.kind === "ingredient" ? { min_quality: form.minQuality ? Number(form.minQuality) : null } : {}),
        },
      },
      {
        onSuccess: () => {
          onClose();
          onNotice(`Saved ${name}.`);
        },
        onError: (e) => {
          const current = staleItem(e);
          if (!current) return;
          // The row shown is replaced by the server's before the list refetches.
          client.setQueryData<ShoppingList>(listKey, (list) =>
            list
              ? {
                  ...list,
                  unchecked: list.unchecked.map((i) => (i.id === current.id ? current : i)),
                  checked: list.checked.map((i) => (i.id === current.id ? current : i)),
                }
              : list,
          );
          onClose();
          onNotice("Updated by someone else; showing the latest.");
        },
      },
    );
  };

  return (
    <Drawer title={`Edit ${name}`} thing="change" dirty={dirty} onClose={onClose} formId={formId} primaryLabel="Save" busy={patch.isPending} busyLabel="Saving…">
      <form id={formId} onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        <AmountRows idPrefix={formId} rows={form.amounts} onChange={(amounts) => setForm((f) => ({ ...f, amounts }))} />
        <Field id={`${formId}-note`} label="Note" value={form.note} maxLength={500} onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))} />
        <ItemFlags
          idPrefix={formId}
          kind={item.kind}
          deferrable={form.deferrable}
          minQuality={form.minQuality}
          onDeferrable={(deferrable) => setForm((f) => ({ ...f, deferrable }))}
          onMinQuality={(minQuality) => setForm((f) => ({ ...f, minQuality }))}
        />
        {invalid ? <Alert tone="error">{invalid}</Alert> : null}
        {patch.isError && !staleItem(patch.error) ? <Alert tone="error">Couldn&apos;t save: {errorMessage(patch.error)}</Alert> : null}
      </form>
    </Drawer>
  );
}
