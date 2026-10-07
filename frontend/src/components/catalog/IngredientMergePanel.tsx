import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import { useMerge, useMergePreview, useStandardEntries, type MergeTarget, type StandardEntry } from "../../api/ingredientLink";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { CategoryChip } from "../CategoryChip";
import { Alert, Button, alertTones, focusRing } from "../ui";
import { Combobox } from "./Combobox";

/**
 * Merging two ingredients, shared by the link page and the ingredient page
 * (03 and 10, 1G; issue 211). Both ask the server for the same trial merge,
 * so the page never predicts what moves on its own.
 */

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** One side of a merge. `products` is shown beside its name when known. */
export interface MergeSide {
  id: string;
  name: string;
  products?: number;
}

/**
 * The inline tomato panel (DV10, DV15): what moves, the unit change and how many
 * prices will need a bridge, the survivor, the measures to copy, and focus
 * starting on Cancel. `targetFor` names what the survivor will be called: the
 * link page's fixed standard name or new name, or, from an ingredient's page,
 * the survivor's own name.
 */
export function IngredientMergePanel({
  first,
  second,
  defaultSurvivor,
  targetFor,
  onCancel,
  onMerged,
}: {
  first: MergeSide;
  second: MergeSide;
  defaultSurvivor: string;
  targetFor: (survivor: MergeSide) => { target: MergeTarget; name: string };
  onCancel: () => void;
  onMerged: (result: { survivor: MergeSide; loser: MergeSide; targetName: string }) => void;
}) {
  const [survivorId, setSurvivorId] = useState(defaultSurvivor);
  const survivor = survivorId === first.id ? first : second;
  const loser = survivor === first ? second : first;
  const { target, name: targetName } = targetFor(survivor);
  const preview = useMergePreview(survivor.id, loser.id, target);
  const merge = useMerge();
  const [unticked, setUnticked] = useState<Set<string>>(new Set());
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => cancel.current?.focus(), []);
  const headingId = `merge-${first.id}-heading`;
  const p = preview.data;
  const moving = p?.products_moving ?? loser.products;
  const copyable = p ? p.measures.filter((m) => m.copyable) : [];
  const stuck = p ? p.measures.filter((m) => !m.copyable) : [];

  return (
    <div role="group" aria-labelledby={headingId} className="flex flex-col gap-3 rounded-md border border-red-300 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950">
      <h3 id={headingId} className="text-sm font-medium">
        Merge {loser.name} into {targetName}?
      </h3>
      <p className="text-sm">
        {moving === undefined ? "Its products move" : `${plural(moving, "product")} ${moving === 1 ? "moves" : "move"}`}. {loser.name} becomes another spelling.
        {p && p.unit_from !== p.unit_to ? ` Units differ (${p.unit_from} → ${p.unit_to})${p.prices_needing_bridge > 0 ? ":" : "."}` : ""}
        {p && p.prices_needing_bridge > 0 ? ` ${plural(p.prices_needing_bridge, "price")} will need a bridge.` : ""}
      </p>
      <fieldset className="flex flex-col gap-1 text-sm">
        <legend className="mb-1">Keep:</legend>
        {[second, first].map((o) => (
          <label key={o.id} className="flex min-h-11 items-center gap-2 lg:min-h-0">
            <input type="radio" name={`${headingId}-keep`} checked={survivorId === o.id} onChange={() => setSurvivorId(o.id)} className={focusRing} />
            {o.name}
            {o.products === undefined ? "" : `, ${plural(o.products, "product")}`}
          </label>
        ))}
      </fieldset>
      {copyable.length > 0 ? (
        <fieldset className="flex flex-col gap-1 text-sm">
          <legend className="mb-1">Copy measures:</legend>
          {copyable.map((m) => (
            <label key={m.label} className="flex min-h-11 items-center gap-2 lg:min-h-0">
              <input
                type="checkbox"
                checked={!unticked.has(m.label)}
                onChange={(e) => {
                  const next = new Set(unticked);
                  if (e.target.checked) next.delete(m.label);
                  else next.add(m.label);
                  setUnticked(next);
                }}
                className={focusRing}
              />
              1 {m.label} = {m.canonical_qty} {p?.unit_from}
            </label>
          ))}
        </fieldset>
      ) : null}
      {stuck.length > 0 ? <p className="text-xs">{stuck.map((m) => m.label).join(", ")} can't carry over: the units differ.</p> : null}
      {preview.isError ? <Alert tone="error">{errorMessage(preview.error)}</Alert> : null}
      {merge.isError ? (
        <div role="alert" className={`rounded-md border px-3 py-2 text-sm ${alertTones.error}`}>
          {errorMessage(merge.error)}
        </div>
      ) : null}
      <p className="text-sm">This can't be undone here.</p>
      <div className="grid grid-cols-2 gap-2 lg:flex">
        <Button
          variant="danger"
          disabled={merge.isPending || !p}
          onClick={() =>
            merge.mutate(
              { survivor_id: survivor.id, loser_id: loser.id, target, copy_measures: copyable.filter((m) => !unticked.has(m.label)).map((m) => m.label) },
              { onSuccess: (done) => onMerged({ survivor, loser, targetName: done.target_name }) },
            )
          }
        >
          {merge.isPending ? "Merging…" : "Merge"}
        </Button>
        <Button ref={cancel} variant="secondary" onClick={onCancel} disabled={merge.isPending}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** A search of the standard list only, for a wrong or missing suggestion (DV4). */
export function ChooseStandard({ id, onChosen, onCancel }: { id: string; onChosen: (e: StandardEntry) => void; onCancel: () => void }) {
  const [text, setText] = useState("");
  const debounced = useDebouncedValue(text, 200);
  const found = useStandardEntries(debounced);
  const items = text.trim() ? (found.data ?? []) : [];
  return (
    <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
      <div className="lg:w-80">
        <Combobox<StandardEntry>
          id={id}
          label="Standard name"
          placeholder="Search the standard list"
          listLabel="Standard names"
          inputValue={text}
          onInputChange={setText}
          items={items}
          autoFocus
          getKey={(e) => e.key}
          status={text.trim() && !found.isFetching && items.length === 0 ? "No standard name matches." : undefined}
          onSelect={onChosen}
          renderItem={(e) => (
            <span className="flex items-center gap-2">
              <span className="font-medium">{e.name}</span>
              <CategoryChip category={e.category} categoryKey={e.category_key} />
            </span>
          )}
        />
      </div>
      <Button variant="secondary" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}
