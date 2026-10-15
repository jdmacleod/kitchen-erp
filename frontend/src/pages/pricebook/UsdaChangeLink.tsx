import { useId, useState } from "react";
import { useUsdaSuggestions } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useUsdaRelink, type UsdaReviewGroup } from "../../api/usdaReview";
import { useNotice } from "../../components/Notice";
import { Alert, Button, Field } from "../../components/ui";

/**
 * "Wrong food? Change the link" on a USDA suggestion row (issue 359): search the
 * loaded USDA foods by name and pick one, or leave the ingredient with none.
 * The row then offers the new food's portions, or leaves the list.
 */
export function UsdaChangeLink({
  group,
  onClose,
}: {
  group: UsdaReviewGroup;
  onClose: () => void;
}) {
  const id = useId();
  const [query, setQuery] = useState(group.name);
  const found = useUsdaSuggestions(query, 5);
  const relink = useUsdaRelink();
  const notice = useNotice();
  const foods = (found.data?.items ?? []).filter((f) => f.fdc_id !== group.fdc_id);

  const choose = (fdcId: number | null, description: string | null) =>
    relink.mutate(
      { ingredientId: group.ingredient_id, fdcId },
      {
        onSuccess: () =>
          notice.show({
            tone: "success",
            message: description
              ? `Linked ${group.name} to “${description}”.`
              : `${group.name} has no USDA food now.`,
          }),
      },
    );

  return (
    <div
      role="group"
      aria-label={`Change the USDA food for ${group.name}`}
      className="flex flex-col gap-3 rounded-md border border-neutral-300 p-3 dark:border-neutral-700"
    >
      <Field
        id={`${id}-q`}
        label="Search USDA foods"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {found.isFetching && foods.length === 0 ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Searching…
        </p>
      ) : foods.length === 0 && query.trim().length >= 2 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          No other USDA food matches “{query.trim()}”.
        </p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label="USDA foods">
          {foods.map((f) => (
            <li key={f.fdc_id} className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0 text-sm">{f.description}</span>
              <Button
                variant="secondary"
                disabled={relink.isPending}
                aria-label={`Use “${f.description}”`}
                onClick={() => choose(f.fdc_id, f.description)}
              >
                Use this
              </Button>
            </li>
          ))}
        </ul>
      )}
      {relink.isError ? <Alert tone="error">{errorMessage(relink.error)}</Alert> : null}
      <div className="grid grid-cols-2 gap-2 lg:flex">
        <Button variant="secondary" disabled={relink.isPending} onClick={() => choose(null, null)}>
          No USDA food
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
