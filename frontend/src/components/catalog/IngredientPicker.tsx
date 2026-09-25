import { useState } from "react";
import { errorMessage } from "../../api/client";
import { useIngredientSearch, type IngredientSummary } from "../../api/catalog";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { Button } from "../ui";
import { Badge, labelClass } from "./fields";
import { Combobox } from "./Combobox";

/** Either an existing ingredient or the name of one to create in the same request. */
export type IngredientChoice =
  | { kind: "existing"; ingredient: IngredientSummary }
  | { kind: "new"; name: string };

type Option = { kind: "existing"; ingredient: IngredientSummary } | { kind: "new"; name: string };

interface IngredientPickerProps {
  id: string;
  label?: string;
  value: IngredientChoice | null;
  onChange: (choice: IngredientChoice | null) => void;
  /** Offer "Create new ingredient" for a name that matches nothing (default true). */
  allowCreate?: boolean;
  disabled?: boolean;
  hint?: string;
}

/**
 * Pick an ingredient by typing part of its name. Once chosen, the choice is
 * shown as text with a Change button so the form does not carry a half-typed
 * search alongside a selection.
 */
export function IngredientPicker({
  id,
  label = "Ingredient",
  value,
  onChange,
  allowCreate = true,
  disabled,
  hint,
}: IngredientPickerProps) {
  const [text, setText] = useState("");
  const debounced = useDebouncedValue(text, 200);
  const search = useIngredientSearch(debounced);
  const trimmed = text.trim();
  const found = trimmed ? (search.data ?? []) : [];
  const settled = debounced === text && !search.isFetching;

  const options: Option[] = found.map((i) => ({
    kind: "existing",
    ingredient: { id: i.id, name: i.name, canonical_unit: i.canonical_unit, active: i.active },
  }));
  const exact = found.some((i) => i.name.toLowerCase() === trimmed.toLowerCase());
  if (allowCreate && trimmed && settled && !exact) {
    options.push({ kind: "new", name: trimmed });
  }

  let status: string | undefined;
  if (trimmed) {
    if (search.isError) status = errorMessage(search.error);
    else if (!settled) status = "Searching…";
    else if (found.length === 0 && !allowCreate) status = "No ingredients match.";
  }

  if (value) {
    return (
      <div className="flex flex-col gap-1">
        <span className={labelClass} id={`${id}-label`}>
          {label}
        </span>
        <div
          className="flex min-h-10 flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-300 bg-neutral-50 px-3 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          role="group"
          aria-labelledby={`${id}-label`}
        >
          <span data-testid={`${id}-choice`}>
            {value.kind === "existing" ? (
              <>
                <span className="font-medium">{value.ingredient.name}</span>
                <span className="text-neutral-600 dark:text-neutral-400"> · {value.ingredient.canonical_unit}</span>
                {value.ingredient.active ? null : (
                  <>
                    {" "}
                    <Badge tone="warn">inactive</Badge>
                  </>
                )}
              </>
            ) : (
              <>
                <span className="font-medium">{value.name}</span> <Badge>new ingredient</Badge>
              </>
            )}
          </span>
          <Button
            variant="ghost"
            className="min-h-8 px-2"
            disabled={disabled}
            onClick={() => {
              setText("");
              onChange(null);
            }}
          >
            Change
          </Button>
        </div>
      </div>
    );
  }

  return (
    <Combobox<Option>
      id={id}
      label={label}
      placeholder="Type to search ingredients"
      hint={hint}
      listLabel="Ingredients"
      inputValue={text}
      onInputChange={setText}
      items={options}
      getKey={(o) => (o.kind === "existing" ? o.ingredient.id : "__new__")}
      status={status}
      disabled={disabled}
      onSelect={(o) => {
        setText("");
        onChange(o);
      }}
      renderItem={(o) =>
        o.kind === "existing" ? (
          <span>
            <span className="font-medium">{o.ingredient.name}</span>
            <span className="text-neutral-600 dark:text-neutral-400"> · {o.ingredient.canonical_unit}</span>
            {o.ingredient.active ? null : (
              <>
                {" "}
                <Badge tone="warn">inactive</Badge>
              </>
            )}
          </span>
        ) : (
          <span>
            Create new ingredient <span className="font-medium">“{o.name}”</span>
          </span>
        )
      }
    />
  );
}
