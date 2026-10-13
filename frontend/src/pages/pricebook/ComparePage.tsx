import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import type { IngredientSummary } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useCompare, type CompareCell, type PriceFilters as Filters } from "../../api/pricebook";
import { Badge } from "../../components/catalog/fields";
import { IngredientPicker } from "../../components/catalog/IngredientPicker";
import { KitchenTag } from "../../components/plan/KitchenTag";
import { PriceAge, formatUnitPrice } from "../../components/pricebook/PriceAge";
import { PriceFilters } from "../../components/pricebook/PriceFilters";
import { SegmentedControl } from "../../components/SegmentedControl";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { useCurrentKitchen } from "../../lib/kitchen";
import { usePageTitle } from "../../lib/usePageTitle";

/** Vendors as columns, ingredients as rows, the best qualifying normalized price in each cell. */
export function ComparePage() {
  usePageTitle("Compare");
  const [chosen, setChosen] = useState<IngredientSummary[]>([]);
  const [filters, setFilters] = useState<Filters>({ min_quality: "", exclude_stale: false, exclude_promo: false });

  // "Kitchen: All · {name}", All by default and kept in the URL (10, Compare
  // prices; UI-8.8): the server restricts to the kitchen's locations.
  const kitchen = useCurrentKitchen();
  const [params, setParams] = useSearchParams();
  const requested = params.get("home_base_id") ?? "";
  const scoped = kitchen.kitchens.find((k) => k.id === requested) ?? null;
  const setKitchen = (id: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (id) next.set("home_base_id", id);
        else next.delete("home_base_id");
        return next;
      },
      { replace: true },
    );
  const compare = useCompare({ ingredient_ids: chosen.map((i) => i.id), ...filters, home_base_id: scoped?.id });
  const shown = compare.data;

  return (
    <>
      <PageHeader title="Compare" tag={scoped ? <KitchenTag name={scoped.name} /> : undefined} description="What each ingredient costs at each vendor, per unit, cheapest marked." />
      <div className="flex flex-col gap-6">
        <Card>
          <div className="flex flex-col gap-3">
            {kitchen.built && kitchen.kitchens.length > 0 ? (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-neutral-600 dark:text-neutral-400">
                  Kitchen
                </span>
                <SegmentedControl label="Kitchen" value={scoped?.id ?? ""} onChange={setKitchen} options={[{ value: "", label: "All" }, ...kitchen.kitchens.map((k) => ({ value: k.id, label: k.name }))]} />
              </div>
            ) : null}
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
            <PriceFilters id="compare" value={filters} onChange={setFilters} brands />
          </div>
        </Card>

        {chosen.length === 0 ? (
          <EmptyState title="Pick ingredients to compare">Each vendor becomes a column; the cheapest qualifying price in a row is marked.</EmptyState>
        ) : compare.isPending || !shown ? (
          <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
            Comparing…
          </p>
        ) : compare.isError ? (
          <Alert tone="error">{errorMessage(compare.error)}</Alert>
        ) : shown.vendors.length === 0 ? (
          <EmptyState
            title="No prices to compare yet"
            action={
              <Link to="/shop/shelf-prices" className={secondaryLinkClass}>
                Log a shelf price
              </Link>
            }
          >
            {chosen.length === 1 ? "This ingredient has" : "These ingredients have"} no prices {scoped ? `at ${scoped.name}'s stores ` : ""}that pass the filters.
          </EmptyState>
        ) : (
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Price comparison">
                <thead>
                  <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                    <th className="py-2 pr-3">Ingredient</th>
                    {shown.vendors.map((v) => (
                      <th key={v.id} className="py-2 pr-3">
                        {v.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                  {shown.rows.map((row) => (
                    <tr key={row.ingredient_id}>
                      <th scope="row" className="py-2 pr-3 text-left font-medium">
                        <Link to={`/catalog/ingredients/${row.ingredient_id}`} className={`rounded underline-offset-2 hover:underline ${focusRing}`}>
                          {row.ingredient_name}
                        </Link>
                        <span className="block text-xs font-normal text-neutral-600 dark:text-neutral-400">per {row.canonical_unit}</span>
                      </th>
                      {shown.vendors.map((v) => {
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
        {formatUnitPrice(cell.display_unit_price, cell.display_unit)} {cell.cheapest ? <Badge tone="good">cheapest</Badge> : null}
      </span>
      <span className="text-xs text-neutral-600 dark:text-neutral-400">
        {cell.brand ? `${cell.brand} ` : ""}
        {cell.product_name}
        {cell.quality_rating !== null ? ` · ${cell.quality_rating}/5` : ""}
        {cell.is_promo ? " · sale" : ""}
        {cell.is_store_brand ? ` · store brand${cell.brand_tier ? `, ${cell.brand_tier}` : ""}` : ""}
      </span>
      <span className="text-xs">
        <PriceAge observedAt={cell.observed_at} ageDays={cell.age_days} stale={cell.stale} />
      </span>
    </div>
  );
}
