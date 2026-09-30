import { useState } from "react";
import { errorMessage } from "../../api/client";
import {
  useIngredientSearch,
  useIngredientsInText,
  type CanonicalUnit,
  type IngredientCreateInput,
  type IngredientMatch,
  type IngredientSummary,
} from "../../api/catalog";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { CategoryChip, type CategoryKey } from "../CategoryChip";
import { Button } from "../ui";
import { Badge, labelClass } from "./fields";
import { Combobox } from "./Combobox";

/**
 * An existing ingredient, the name of one to create, or a standard-list entry
 * to create. Nothing is created until the form saves: a new or standard choice
 * travels with the product in the same request (DV22).
 */
export type IngredientChoice =
  | { kind: "existing"; ingredient: IngredientSummary; matchedSpelling?: string | null }
  | { kind: "new"; name: string }
  | {
      kind: "standard";
      key: string;
      name: string;
      canonical_unit: CanonicalUnit;
      category: string | null;
      category_key: CategoryKey | null;
    };

type Option =
  | { kind: "existing"; match: IngredientMatch }
  | { kind: "standard"; match: IngredientMatch }
  | { kind: "new"; name: string };

/** The ingredient's name, whichever kind of choice it is. */
export function choiceName(choice: IngredientChoice): string {
  return choice.kind === "existing" ? choice.ingredient.name : choice.name;
}

/** The canonical unit the chosen ingredient has, or will have ("g" for a new one). */
export function choiceUnit(choice: IngredientChoice): CanonicalUnit {
  if (choice.kind === "existing") return choice.ingredient.canonical_unit;
  if (choice.kind === "standard") return choice.canonical_unit;
  return "g";
}

/** The product request fields that name this ingredient: an id, or one to create. */
export function choiceInput(
  choice: IngredientChoice,
): { ingredient_id: string } | { ingredient: IngredientCreateInput } {
  if (choice.kind === "existing") return { ingredient_id: choice.ingredient.id };
  if (choice.kind === "standard") return { ingredient: { name: choice.name, standard_key: choice.key } };
  return { ingredient: { name: choice.name } };
}

function summary(m: IngredientMatch): IngredientSummary {
  return {
    id: m.id ?? "",
    name: m.name,
    canonical_unit: m.canonical_unit,
    active: m.active,
    category: m.category,
    category_key: m.category_key,
  };
}

interface IngredientPickerProps {
  id: string;
  label?: string;
  value: IngredientChoice | null;
  onChange: (choice: IngredientChoice | null) => void;
  /** Offer "Create new ingredient" for a name that matches nothing (default true). */
  allowCreate?: boolean;
  /**
   * Offer standard-list names the catalog doesn't have yet. Defaults to
   * `allowCreate`: only a picker that may create an ingredient offers them (O5).
   */
  offerStandard?: boolean;
  disabled?: boolean;
  hint?: string;
  /** Told what is typed in the search, so a form can count it as unsaved input. */
  onTextChange?: (text: string) => void;
  /** A receipt line: ingredients it names outright are offered in one click (#88). */
  suggestFrom?: string;
}

/**
 * Pick an ingredient by typing part of its name or any other spelling. Once
 * chosen, the choice is shown as text with a Change button so the form does not
 * carry a half-typed search alongside a selection.
 *
 * Rows: an exact name or spelling first, other catalog matches, then "From the
 * standard list", then "Create new", which is hidden when the text already
 * names something (DV9, DV23).
 */
export function IngredientPicker({
  id,
  label = "Ingredient",
  value,
  onChange,
  allowCreate = true,
  offerStandard = allowCreate,
  disabled,
  hint,
  onTextChange,
  suggestFrom = "",
}: IngredientPickerProps) {
  const [text, setTextState] = useState("");
  const setText = (next: string) => {
    setTextState(next);
    onTextChange?.(next);
  };
  const debounced = useDebouncedValue(text, 200);
  const search = useIngredientSearch(debounced, offerStandard);
  const trimmed = text.trim();
  const found = trimmed ? (search.data ?? []) : [];
  const inLine = useIngredientsInText(value ? "" : suggestFrom);
  // Standard names only where the picker may create one (O5).
  const named = (inLine.data ?? []).filter((m) => m.kind === "ingredient" || offerStandard);
  const settled = debounced === text && !search.isFetching;

  const options: Option[] = [
    ...found.filter((m) => m.kind === "ingredient").map((match): Option => ({ kind: "existing", match })),
    ...(offerStandard ? found.filter((m) => m.kind === "standard").map((match): Option => ({ kind: "standard", match })) : []),
  ];
  const taken = found.some((m) => m.exact || m.name.toLowerCase() === trimmed.toLowerCase());
  if (allowCreate && trimmed && settled && !taken) {
    options.push({ kind: "new", name: trimmed });
  }

  let status: string | undefined;
  if (trimmed) {
    if (search.isError) status = errorMessage(search.error);
    else if (!settled) status = "Searching…";
    else if (found.length === 0 && !allowCreate) status = "No ingredients match.";
  }

  if (value) {
    const note =
      value.kind === "existing" && value.matchedSpelling
        ? `matched ${value.matchedSpelling}`
        : value.kind === "standard"
          ? "New, from the standard list"
          : null;
    return (
      <div className="flex flex-col gap-1">
        <span className={labelClass} id={`${id}-label`}>
          {label}
        </span>
        <div
          className="flex min-h-11 lg:min-h-10 flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-300 bg-neutral-50 px-3 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          role="group"
          aria-labelledby={`${id}-label`}
          aria-describedby={note ? `${id}-note` : undefined}
        >
          <span data-testid={`${id}-choice`}>
            {value.kind === "new" ? (
              <>
                <span className="font-medium">{value.name}</span> <Badge>new ingredient</Badge>
              </>
            ) : (
              <>
                <span className="font-medium">{choiceName(value)}</span>
                <span className="text-neutral-600 dark:text-neutral-400"> · {choiceUnit(value)}</span>
                {value.kind === "existing" && !value.ingredient.active ? (
                  <>
                    {" "}
                    <Badge tone="warn">inactive</Badge>
                  </>
                ) : null}
              </>
            )}
          </span>
          <Button
            variant="ghost"
            className="min-h-11 lg:min-h-8 px-2"
            disabled={disabled}
            onClick={() => {
              setText("");
              onChange(null);
            }}
          >
            Change
          </Button>
        </div>
        {note ? (
          <p id={`${id}-note`} className="text-xs text-neutral-600 dark:text-neutral-400" data-testid={`${id}-note`}>
            {note}
          </p>
        ) : null}
      </div>
    );
  }

  const select = (o: Option) => {
    setText("");
    if (o.kind === "existing") {
      onChange({ kind: "existing", ingredient: summary(o.match), matchedSpelling: o.match.matched_spelling });
    } else if (o.kind === "standard") {
      const m = o.match;
      onChange({
        kind: "standard",
        key: m.key ?? "",
        name: m.name,
        canonical_unit: m.canonical_unit,
        category: m.category,
        category_key: m.category_key,
      });
    } else {
      onChange(o);
    }
  };

  const combobox = (
    <Combobox<Option>
      id={id}
      label={label}
      placeholder="Type to search ingredients"
      hint={hint}
      listLabel="Ingredients"
      inputValue={text}
      onInputChange={setText}
      items={options}
      getKey={(o) => (o.kind === "existing" ? `i:${o.match.id}` : o.kind === "standard" ? `s:${o.match.key}` : "__new__")}
      groupOf={(o) => (o.kind === "standard" ? "From the standard list" : null)}
      getLabel={(o) =>
        o.kind !== "new" && o.match.matched_spelling
          ? `${o.match.matched_spelling}, another name for ${o.match.name}`
          : undefined
      }
      status={status}
      disabled={disabled}
      onSelect={select}
      renderItem={(o) => {
        if (o.kind === "new") {
          return (
            <span>
              Create new ingredient <span className="font-medium">“{o.name}”</span>
            </span>
          );
        }
        const m = o.match;
        return (
          <span className="flex flex-wrap items-center gap-x-2">
            <span>
              <span className="font-medium">{m.name}</span>
              {o.kind === "existing" ? (
                <span className="text-neutral-600 dark:text-neutral-400"> · {m.canonical_unit}</span>
              ) : null}
            </span>
            {o.kind === "standard" ? <CategoryChip category={m.category} categoryKey={m.category_key} /> : null}
            {m.matched_spelling ? (
              <span className="text-xs text-neutral-600 dark:text-neutral-400">matches {m.matched_spelling}</span>
            ) : null}
            {o.kind === "existing" && !m.active ? <Badge tone="warn">inactive</Badge> : null}
          </span>
        );
      }}
    />
  );
  if (named.length === 0 || trimmed) return combobox;
  // The line names an ingredient outright: offered, never chosen unasked.
  return (
    <div className="flex flex-col gap-1">
      {combobox}
      <div role="group" aria-label="Named on the receipt line" className="flex flex-wrap items-center gap-1 text-xs">
        <span className="text-neutral-600 dark:text-neutral-400">On the receipt:</span>
        {named.map((m) => (
          <Button
            key={m.kind === "ingredient" ? `i:${m.id}` : `s:${m.key}`}
            variant="secondary"
            className="min-h-11 lg:min-h-8 px-2 text-xs"
            disabled={disabled}
            onClick={() => select(m.kind === "ingredient" ? { kind: "existing", match: m } : { kind: "standard", match: m })}
          >
            {m.name}
            {m.kind === "standard" ? <span className="text-neutral-600 dark:text-neutral-400">&nbsp;· new</span> : null}
          </Button>
        ))}
      </div>
    </div>
  );
}
