import { useState } from "react";
import { Link } from "react-router";
import type { IngredientSummary } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useCompare, type CompareCell, type PriceFilters as Filters } from "../../api/pricebook";
import { Badge } from "../../components/catalog/fields";
import { IngredientPicker } from "../../components/catalog/IngredientPicker";
import { PriceAge, formatUnitPrice } from "../../components/pricebook/PriceAge";
import { PriceFilters } from "../../components/pricebook/PriceFilters";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

/** Vendors as columns, ingredients as rows, the best qualifying normalized price in each cell. */
export function ComparePage() {
  usePageTitle("Compare");
  const [chosen, setChosen] = useState<IngredientSummary[]>([]);
  const [filters, setFilters] = useState<Filters>({ min_quality: "", exclude_stale: false, exclude_promo: false });
  const compare = useCompare({ ingredient_ids: chosen.map((i) => i.id), ...filters });

  return (
    <>
      <PageHeader title="Compare" />
      <div className="flex flex-col gap-6">
        <Card>
          <div className="flex flex-col gap-3">
            <IngredientPicker
              id="compare-ingredient"
              label="Add an ingredient"
              value={null}
              allowCreate={false}
              onChange={(choice) => {
                if (choice?.kind === "existing" && !chosen.some((i) => i.id === choice.ingredient.id)) setChosen([...chosen, choice.ingredient]);
              }}
              hint="Type part of a name and pick. Each pick adds a row."
            />
            {chosen.length > 0 ? (
              <ul aria-label="Chosen ingredients" className="flex flex-wrap gap-2">
                {chosen.map((i) => (
                  <li key={i.id} className="inline-flex items-center gap-1 rounded-md border border-neutral-300 bg-neutral-50 py-1 pr-1 pl-2 text-sm dark:border-neutral-700 dark:bg-neutral-900">
                    {i.name}
                    <Button variant="ghost" className="min-h-6 px-1.5 text-xs" aria-label={`Remove ${i.name}`} onClick={() => setChosen(chosen.filter((c) => c.id !== i.id))}>
                      ×
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
            <PriceFilters id="compare" value={filters} onChange={setFilters} />
          </div>
        </Card>

        {chosen.length === 0 ? (
          <EmptyState title="Pick ingredients to compare">Each vendor becomes a column; the cheapest qualifying price in a row is marked.</EmptyState>
        ) : compare.isPending ? (
          <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
            Comparing…
          </p>
        ) : compare.isError ? (
          <Alert tone="error">{errorMessage(compare.error)}</Alert>
        ) : (
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Price comparison">
                <thead>
                  <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                    <th className="py-2 pr-3">Ingredient</th>
                    {compare.data.vendors.map((v) => (
                      <th key={v.id} className="py-2 pr-3">
                        {v.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                  {compare.data.rows.map((row) => (
                    <tr key={row.ingredient_id}>
                      <th scope="row" className="py-2 pr-3 text-left font-medium">
                        <Link to={`/catalog/ingredients/${row.ingredient_id}`} className={`rounded underline-offset-2 hover:underline ${focusRing}`}>
                          {row.ingredient_name}
                        </Link>
                        <span className="block text-xs font-normal text-neutral-600 dark:text-neutral-400">per {row.canonical_unit}</span>
                      </th>
                      {compare.data.vendors.map((v) => {
                        const cell = row.cells[v.id];
                        return (
                          <td key={v.id} className="py-2 pr-3 align-top" data-testid={`cell-${row.ingredient_id}-${v.id}`} title={cell ? undefined : "No price known"}>
                            {cell ? <CellView cell={cell} /> : null}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {compare.isFetching && compare.isPlaceholderData ? (
              <p role="status" className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
                Updating…
              </p>
            ) : null}
          </Card>
        )}
      </div>
    </>
  );
}

function CellView({ cell }: { cell: CompareCell }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className={`tabular-nums ${cell.cheapest ? "font-bold" : ""}`}>
        {formatUnitPrice(cell.norm_unit_price, cell.norm_unit)} {cell.cheapest ? <Badge tone="good">cheapest</Badge> : null}
      </span>
      <span className="text-xs text-neutral-600 dark:text-neutral-400">
        {cell.brand ? `${cell.brand} ` : ""}
        {cell.product_name}
        {cell.quality_rating !== null ? ` · ${cell.quality_rating}/5` : ""}
        {cell.is_promo ? " · sale" : ""}
      </span>
      <span className="text-xs">
        <PriceAge observedAt={cell.observed_at} ageDays={cell.age_days} stale={cell.stale} />
      </span>
    </div>
  );
}
