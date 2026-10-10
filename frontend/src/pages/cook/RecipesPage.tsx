import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import { useInbox } from "../../api/inbox";
import {
  formatCostRange,
  pricedSentence,
  servingsText,
  useRecipes,
  useRecipesStatus,
  type Completeness,
  type RecipeListItem,
  type RecipeStatus,
} from "../../api/recipes";
import { RecipeBadges } from "../../components/cook/RecipeBadges";
import { RescanButton, useRescanWithNotice } from "../../components/cook/RescanButton";
import { SegmentedControl } from "../../components/SegmentedControl";
import { Alert, Button, EmptyState, PageHeader, focusRing, tapTarget } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatRelative } from "../../lib/format";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";

type StatusFilter = "all" | "parse_error" | "missing";
type CompletenessFilter = "all" | Completeness;

const STATUS_WORDS: Record<Exclude<StatusFilter, "all">, string> = {
  parse_error: "that can't be read",
  missing: "that are missing",
};
const COMPLETENESS_WORDS: Record<Completeness, string> = {
  complete: "with a complete cost",
  incomplete: "with an incomplete cost",
};

function parseStatus(value: string | null): StatusFilter {
  return value === "parse_error" || value === "missing" ? value : "all";
}
function parseCompleteness(value: string | null): CompletenessFilter {
  return value === "complete" || value === "incomplete" ? value : "all";
}

/**
 * The recipes list (10, Cook: recipes; UI-7.3–7.6): what each dish costs and how
 * much of that figure is known. Search, status and completeness live in the URL
 * and run on the server; rows are ordered by title.
 */
export function RecipesPage() {
  usePageTitle("Recipes");
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const status = parseStatus(params.get("status"));
  const completeness = parseCompleteness(params.get("completeness"));
  const setParam = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  // The field follows ?q= when it changes from outside (Back, a link), and the
  // URL takes only text the debounce has settled on.
  const [text, setText] = useState(q);
  const [seenQ, setSeenQ] = useState(q);
  if (q !== seenQ) {
    setSeenQ(q);
    setText(q);
  }
  const debounced = useDebouncedValue(text.trim(), 250);
  useEffect(() => {
    if (debounced === text.trim() && debounced !== q) setParam("q", debounced || null);
    // Only the typed text drives the URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const list = useRecipes({
    q: q || undefined,
    status: status === "all" ? undefined : (status as RecipeStatus),
    completeness: completeness === "all" ? undefined : completeness,
  });
  const repo = useRecipesStatus();
  const filtered = q !== "" || status !== "all" || completeness !== "all";

  // The palette's "Rescan recipes" lands here with ?rescan=1 (UI-7.17): run it once
  // and drop the parameter so Back does not rescan again.
  const rescan = useRescanWithNotice();
  const askedToRescan = params.get("rescan") === "1";
  useEffect(() => {
    if (!askedToRescan) return;
    setParam("rescan", null);
    rescan.run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askedToRescan]);

  // Names waiting (10, Names waiting): the inbox's one recipe row, as the Ingredients list does for skipped rows.
  const waiting = useInbox().data?.items.find((i) => i.kind === "recipe");

  const clearFilters = () => {
    setText("");
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const key of ["q", "status", "completeness"]) next.delete(key);
        return next;
      },
      { replace: true },
    );
  };

  const counts = repo.data?.counts;
  const withCount = (label: string, n: number | undefined) => (n === undefined ? label : `${label} (${n})`);
  const items = list.data?.items ?? [];

  return (
    <>
      <PageHeader title="Recipes" description="What each dish costs from the price book, and how much of that figure is known.">
        <RescanButton />
      </PageHeader>

      {waiting ? (
        <p className="mb-4 text-sm text-neutral-700 dark:text-neutral-300" data-testid="names-waiting-line">
          {waiting.title} ·{" "}
          <Link to={waiting.action_route} className={`rounded font-medium underline ${focusRing}`}>
            {waiting.action_label}
          </Link>
        </p>
      ) : null}

      <div className="mb-4 flex flex-col gap-3">
        <label htmlFor="recipe-search" className="sr-only">
          Search recipes
        </label>
        <input
          id="recipe-search"
          type="text"
          enterKeyHint="search"
          autoComplete="off"
          maxLength={200}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search by title or path"
          className={`min-h-12 w-full rounded-lg border border-neutral-300 bg-white px-4 text-base dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
        />
        {/* Below lg the controls scroll sideways as chips (10, Phone). */}
        <div className="-mx-4 flex gap-3 overflow-x-auto px-4 pb-1 lg:mx-0 lg:flex-wrap lg:overflow-visible lg:px-0">
          <SegmentedControl<StatusFilter>
            label="Status"
            options={[
              { value: "all", label: withCount("All", repo.data?.total) },
              { value: "parse_error", label: withCount("Can't read", counts?.parse_error) },
              { value: "missing", label: withCount("Missing", counts?.missing) },
            ]}
            value={status}
            onChange={(v) => setParam("status", v === "all" ? null : v)}
          />
          <SegmentedControl<CompletenessFilter>
            label="Completeness"
            options={[
              { value: "all", label: "All" },
              { value: "complete", label: "Complete" },
              { value: "incomplete", label: "Incomplete" },
            ]}
            value={completeness}
            onChange={(v) => setParam("completeness", v === "all" ? null : v)}
          />
        </div>
      </div>

      {list.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : list.isError ? (
        <Alert tone="error">
          <span className="flex flex-wrap items-center justify-between gap-2">
            <span>Couldn&apos;t load recipes: {errorMessage(list.error)}</span>
            <Button variant="secondary" onClick={() => void list.refetch()}>
              Try again
            </Button>
          </span>
        </Alert>
      ) : items.length === 0 ? (
        filtered ? (
          <EmptyState
            title={filteredEmptyTitle(q, status, completeness)}
            action={
              <Button variant="secondary" onClick={clearFilters}>
                Clear filters
              </Button>
            }
          />
        ) : (
          // No repository (the directory missing, empty or without .cook files) is an
          // empty state, not an error: the application is working as configured (UI-7.5).
          <EmptyState title="No recipes found" action={<RescanButton />}>
            Point <code className="font-mono">RECIPES_PATH</code> at a Cooklang repository, or run <code className="font-mono">make seed-examples</code> to start with
            the example recipes.
          </EmptyState>
        )
      ) : (
        <div className="rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          <RecipeRows items={items} />
        </div>
      )}
    </>
  );
}

/** "No recipes match 'soup' that can't be read" (10, States). */
export function filteredEmptyTitle(q: string, status: StatusFilter, completeness: CompletenessFilter): string {
  const parts = [
    q ? `match ‘${q}’` : null,
    status !== "all" ? STATUS_WORDS[status] : null,
    completeness !== "all" ? COMPLETENESS_WORDS[completeness] : null,
  ].filter((p): p is string => p !== null);
  return `No recipes ${parts.join(" ")}`;
}

const headCell = `px-3 py-2 text-left text-xs font-semibold ${muted}`;
// Recipe, Servings, Cost, Basket, Known, Indexed.
const columns = "lg:grid-cols-[minmax(0,2.4fr)_minmax(0,0.7fr)_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1.1fr)]";

/**
 * The rows: a grid table at lg and wider; below it each row stacks as a block
 * (title and badges, the path, then cost and known on one line), so a phone
 * never scrolls sideways (UI-7.18).
 */
function RecipeRows({ items }: { items: RecipeListItem[] }) {
  return (
    <div role="table" aria-label="Recipes" className="text-sm">
      <div role="rowgroup" className="hidden lg:block">
        <div role="row" className={`grid border-b border-neutral-200 dark:border-neutral-800 ${columns}`}>
          <div role="columnheader" className={headCell}>
            Recipe
          </div>
          <div role="columnheader" className={headCell}>
            Servings
          </div>
          <div role="columnheader" className={`${headCell} lg:text-right`}>
            Cost
          </div>
          <div role="columnheader" className={`${headCell} lg:text-right`}>
            Basket
          </div>
          <div role="columnheader" className={headCell}>
            Known
          </div>
          <div role="columnheader" className={headCell}>
            Indexed
          </div>
        </div>
      </div>
      <div role="rowgroup" className="divide-y divide-neutral-200 dark:divide-neutral-800">
        {items.map((r) => (
          <RecipeRow key={r.id} recipe={r} />
        ))}
      </div>
    </div>
  );
}

function RecipeRow({ recipe }: { recipe: RecipeListItem }) {
  const cost = recipe.cost;
  const servings = servingsText(recipe);
  const partial = cost !== null && cost.lines_priced < cost.lines_total;
  return (
    <div role="row" className={`grid gap-x-3 gap-y-1 px-3 py-3 lg:items-start lg:py-2 ${columns}`} data-testid="recipe-row">
      <div role="cell" className="min-w-0">
        <span className="flex flex-wrap items-center gap-2">
          <Link to={`/cook/recipes/${recipe.id}`} className={`${tapTarget} rounded-md font-semibold underline-offset-2 hover:underline ${focusRing}`}>
            {recipe.title}
          </Link>
          <RecipeBadges recipe={recipe} />
        </span>
        <span className={`block truncate font-mono text-xs ${muted}`}>{recipe.path}</span>
      </div>
      <div role="cell" className={`text-xs lg:text-sm ${muted} lg:text-neutral-900 lg:dark:text-neutral-100`}>
        {servings ? (
          <>
            <span className="lg:hidden">Serves </span>
            {servings}
          </>
        ) : (
          <span aria-label="Servings not given">—</span>
        )}
      </div>
      <div role="cell" className="tabular-nums lg:text-right">
        {cost ? (
          <>
            <span>
              <span className="lg:hidden">Cost </span>
              {formatCostRange(cost.consumed_cost, cost.consumed_cost_high)}
            </span>
            {cost.per_serving ? <span className={`block text-xs ${muted}`}>{formatMoney(cost.per_serving)} per serving</span> : null}
            {cost.provisional ? <span className={`block text-xs ${muted}`}>provisional</span> : null}
          </>
        ) : (
          <span className={muted}>Not costed yet</span>
        )}
      </div>
      <div role="cell" className={`tabular-nums lg:text-right ${cost ? "" : "hidden lg:block"}`}>
        {cost ? (
          <>
            <span className="lg:hidden">Basket </span>
            {formatCostRange(cost.basket_cost, cost.basket_cost_high)}
          </>
        ) : (
          <span className={muted}>—</span>
        )}
      </div>
      <div role="cell" className={cost ? (partial ? "text-amber-900 dark:text-amber-200" : "") : "hidden lg:block"}>
        {cost ? pricedSentence(cost.lines_priced, cost.lines_total) : <span className={muted}>—</span>}
      </div>
      <div role="cell" className={`text-xs lg:text-sm ${muted}`}>
        <span className="lg:hidden">Indexed </span>
        <time dateTime={recipe.last_indexed_at}>{formatRelative(recipe.last_indexed_at)}</time>
      </div>
    </div>
  );
}
