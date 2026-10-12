import { useState } from "react";
import { servingsText, type RecipeListItem } from "../../api/recipes";
import { Combobox } from "../catalog/Combobox";
import { labelClass } from "../catalog/fields";
import { Button } from "../ui";

interface RecipePickerProps {
  id: string;
  recipes: RecipeListItem[];
  loading: boolean;
  error: string | null;
  value: RecipeListItem | null;
  onChange: (recipe: RecipeListItem | null) => void;
}

/** A typeahead over the indexed recipes, matched by title; the chosen one shows with Change. */
export function RecipePicker({ id, recipes, loading, error, value, onChange }: RecipePickerProps) {
  const [text, setText] = useState("");
  const q = text.trim().toLocaleLowerCase();
  const items = q ? recipes.filter((r) => r.title.toLocaleLowerCase().includes(q)) : recipes;

  if (value) {
    const servings = servingsText(value);
    return (
      <div className="flex flex-col gap-1">
        <span className={labelClass} id={`${id}-label`}>
          Recipe
        </span>
        <div role="group" aria-labelledby={`${id}-label`} className="flex min-h-11 flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-300 bg-neutral-50 px-3 py-1 text-sm lg:min-h-10 dark:border-neutral-700 dark:bg-neutral-900">
          <span data-testid={`${id}-choice`}>
            <span className="font-medium">{value.title}</span>
            {servings ? <span className="text-neutral-600 dark:text-neutral-400"> · {servings} servings</span> : null}
          </span>
          <Button variant="ghost" className="min-h-11 px-2 lg:min-h-8" onClick={() => onChange(null)}>
            Change
          </Button>
        </div>
      </div>
    );
  }

  let status: string | undefined;
  if (error) status = error;
  else if (loading) status = "Loading recipes…";
  else if (recipes.length === 0) status = "No recipes are indexed yet.";
  else if (items.length === 0) status = "No recipes match.";

  return (
    <Combobox<RecipeListItem>
      id={id}
      label="Recipe"
      placeholder="Type to search recipes"
      listLabel="Recipes"
      inputValue={text}
      onInputChange={setText}
      items={items}
      getKey={(r) => r.id}
      status={status}
      onSelect={(r) => {
        setText("");
        onChange(r);
      }}
      renderItem={(r) => {
        const servings = servingsText(r);
        return (
          <span>
            <span className="font-medium">{r.title}</span>
            {servings ? <span className="text-neutral-600 dark:text-neutral-400"> · {servings} servings</span> : null}
          </span>
        );
      }}
    />
  );
}
