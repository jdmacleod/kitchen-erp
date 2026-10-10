import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import {
  COST_BASES,
  basisLabel,
  servingsText,
  useRecipe,
  useRecipeCost,
  useRecipeCostHistory,
  useRelinkRecipe,
  type CostBasis,
  type Recipe,
} from "../../api/recipes";
import { CostTable, CostTotals } from "../../components/cook/CostTable";
import { RecipeBadges } from "../../components/cook/RecipeBadges";
import { RemoveRecipe } from "../../components/cook/RemoveRecipe";
import { RenderedRecipe } from "../../components/cook/RenderedRecipe";
import { RescanButton } from "../../components/cook/RescanButton";
import { SnapshotChart, snapshotPoints } from "../../components/cook/SnapshotChart";
import { useNotice } from "../../components/Notice";
import { SegmentedControl } from "../../components/SegmentedControl";
import { Alert, Button, EmptyState, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { formatRelative } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";

function parseBasis(value: string | null): CostBasis {
  return value === "average" || value === "cheapest" ? value : "latest";
}

export function RecipeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const recipe = useRecipe(id);
  usePageTitle(recipe.data?.title ?? "Recipe");

  if (recipe.isPending) {
    return (
      <>
        <Breadcrumb />
        <PageHeader title="Recipe" />
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      </>
    );
  }
  if (recipe.isError) {
    const missing = isApiError(recipe.error) && recipe.error.status === 404;
    return (
      <>
        <Breadcrumb />
        <PageHeader title="Recipe" />
        {missing ? (
          <EmptyState title="No such recipe">
            <Link to="/cook/recipes" className={`rounded-md underline ${focusRing}`}>
              Back to recipes
            </Link>
          </EmptyState>
        ) : (
          <Alert tone="error">
            <span className="flex flex-wrap items-center justify-between gap-2">
              <span>Couldn&apos;t load this recipe: {errorMessage(recipe.error)}</span>
              <Button variant="secondary" onClick={() => void recipe.refetch()}>
                Try again
              </Button>
            </span>
          </Alert>
        )}
      </>
    );
  }
  return <RecipeDetail recipe={recipe.data} />;
}

function Breadcrumb() {
  return (
    <nav aria-label="Breadcrumb" className={`mb-2 text-sm ${muted}`}>
      <span>Cook</span> <span aria-hidden="true">/</span>{" "}
      <Link to="/cook/recipes" className={`inline-flex min-h-11 items-center rounded underline lg:min-h-0 ${focusRing}`}>
        Recipes
      </Link>
    </nav>
  );
}

/**
 * The recipe page (10, Recipe page; UI-7.7–7.9, 7.13, 7.15): the rendered file
 * beside the cost table at 1024px and wider, with the cost table in the wider
 * column; one column below, totals and basis first, with "Jump to recipe".
 */
function RecipeDetail({ recipe }: { recipe: Recipe }) {
  const [params, setParams] = useSearchParams();
  const basis = parseBasis(params.get("basis"));
  const setBasis = (b: CostBasis) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (b === "latest") next.delete("basis");
        else next.set("basis", b);
        return next;
      },
      { replace: true },
    );

  // A basis without a snapshot is computed inside the call, so "Costing…" shows
  // until it answers (UI-7.8). A missing file keeps its last snapshot.
  const cost = useRecipeCost(recipe.id, basis);
  const history = useRecipeCostHistory(recipe.id, basis);
  const points = snapshotPoints(history.data?.items ?? [], cost.data);

  const servings = servingsText(recipe);
  const meta = (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="font-mono text-xs">{recipe.path}</span>
      {servings ? (
        <>
          <span aria-hidden="true">·</span>
          <span>Serves {servings}</span>
        </>
      ) : null}
      <span aria-hidden="true">·</span>
      <span>
        Indexed <time dateTime={recipe.last_indexed_at}>{formatRelative(recipe.last_indexed_at)}</time>
      </span>
    </span>
  );

  return (
    <>
      <Breadcrumb />
      <PageHeader
        title={recipe.title}
        description={
          <span className="flex flex-col gap-1">
            <RecipeBadges recipe={recipe} />
            {meta}
          </span>
        }
      >
        <div className="flex flex-wrap gap-2">
          <a href="#recipe" className={`${secondaryLinkClass} lg:hidden`}>
            Jump to recipe
          </a>
          {recipe.status === "missing" ? <RemoveRecipe recipe={recipe} /> : null}
          <RescanButton />
        </div>
      </PageHeader>

      {recipe.status === "parse_error" ? (
        <Alert tone="warn" className="mb-4">
          This file can&apos;t be read: {recipe.parse_error_message ?? "the parser gave no message"}.
        </Alert>
      ) : null}
      {recipe.status === "missing" ? <MissingAlert recipe={recipe} /> : null}

      <div className="flex flex-col gap-6 lg:grid lg:grid-cols-[1fr_1.4fr] lg:items-start">
        <div className="order-2 min-w-0 lg:order-1">
          <RenderedRecipe recipe={recipe} id="recipe" />
        </div>
        <div className="order-1 flex min-w-0 flex-col gap-4 lg:order-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-medium">Cost</h2>
            <SegmentedControl<CostBasis> label="Basis" options={COST_BASES.map((b) => ({ value: b, label: basisLabel[b] }))} value={basis} onChange={setBasis} />
          </div>
          {recipe.status === "parse_error" && cost.data ? <p className={`text-sm ${muted}`}>Showing the last version that could be read.</p> : null}
          {cost.isError ? (
            <Alert tone="error">
              <span className="flex flex-wrap items-center justify-between gap-2">
                <span>Couldn&apos;t cost this recipe: {errorMessage(cost.error)}</span>
                <Button variant="secondary" onClick={() => void cost.refetch()}>
                  Try again
                </Button>
              </span>
            </Alert>
          ) : (
            <>
              <CostTotals cost={cost.data} costing={cost.isPending} />
              {cost.data ? <CostTable cost={cost.data} /> : null}
              {history.isError ? <Alert tone="error">Couldn&apos;t load the cost history: {errorMessage(history.error)}</Alert> : <SnapshotChart points={points} />}
            </>
          )}
        </div>
      </div>
    </>
  );
}

/**
 * The missing recipe's neutral alert with its relink proposal (10, States; UI-7.15).
 * "Not the same" puts the proposal away for this visit; a later scan may make another.
 */
function MissingAlert({ recipe }: { recipe: Recipe }) {
  const relink = useRelinkRecipe(recipe.id);
  const notice = useNotice();
  const [dismissed, setDismissed] = useState<string | null>(null);
  const proposal = recipe.relink && recipe.relink.target_id !== dismissed ? recipe.relink : null;
  return (
    <Alert tone="info" className="mb-4">
      <span className="flex flex-col gap-2">
        <span>This file is no longer in the repository. Its costs and pins are kept until you remove it.</span>
        {proposal ? (
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="relink-proposal">
            <span>
              Is it now ‘{proposal.title}’ (<span className="font-mono text-xs">{proposal.path}</span>)?
            </span>
            <Button
              variant="secondary"
              disabled={relink.isPending}
              onClick={() =>
                relink.mutate(proposal.target_id, {
                  onSuccess: () => notice.show({ tone: "success", message: `Relinked to ${proposal.title}.` }),
                  onError: (e) => notice.show({ tone: "error", message: `Couldn't relink: ${errorMessage(e)}` }),
                })
              }
            >
              {relink.isPending ? "Relinking…" : "Relink"}
            </Button>
            <Button variant="ghost" disabled={relink.isPending} onClick={() => setDismissed(proposal.target_id)}>
              Not the same
            </Button>
          </span>
        ) : null}
      </span>
    </Alert>
  );
}
