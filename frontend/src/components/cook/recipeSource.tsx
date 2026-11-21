// Cost lines costed from a recipe (issue 526): a recipe reference, `@./basics/
// simple-syrup{1%fl oz}`, or a line on an ingredient a house recipe makes. The
// words for each outcome live here, one row per failure code, so the table and
// the tests read one source. The UI says "makes", never "yield"; `yield:` is
// file syntax, shown in code. Every fix on the recipe side names the file to edit
// and ends "then rescan": the edit happens in the recipes repository.

import { Fragment, type ReactNode } from "react";
import { Link } from "react-router";
import type { CostLine } from "../../api/recipeCosts";
import { makesShort, YIELD_EXAMPLE, type RecipeLink } from "../../api/recipeSources";
import { focusRing, tapTarget } from "../ui";
import { PostedBadge } from "./posted";

const muted = "text-neutral-600 dark:text-neutral-400";
const link = `${tapTarget} rounded-sm font-medium underline ${focusRing}`;

/** The failure codes only a line from a recipe has. */
export const RECIPE_CODES = new Set([
  "no_yield",
  "bad_yield",
  "yield_unit",
  "no_recipe",
  "cycle",
  "recipe_unpriced",
  "recipe_unreadable",
]);

/** Whether a line costs, or tried to cost, from a recipe. */
export function fromRecipe(line: CostLine): boolean {
  return (
    Boolean(line.source_recipe) ||
    line.line.resolution === "recipe" ||
    RECIPE_CODES.has(line.failure_code ?? "")
  );
}

function recipeHref(recipe: RecipeLink): string {
  return `/cook/recipes/${recipe.id}`;
}

function RecipeTitleLink({ recipe, suffix = "" }: { recipe: RecipeLink; suffix?: string }) {
  return (
    <Link to={recipeHref(recipe)} className={link}>
      {recipe.title}
      {suffix}
    </Link>
  );
}

function Code({ children }: { children: ReactNode }) {
  return <code className="font-mono text-xs">{children}</code>;
}

/** The line's quantity as written: "1 fl oz". */
function lineAmount(line: CostLine): string {
  const r = line.line;
  const qty = r.qty_high && r.qty_high !== r.qty ? `${r.qty}–${r.qty_high}` : (r.qty ?? "");
  return [qty, r.unit_text].filter(Boolean).join(" ");
}

/**
 * The Ingredient cell of a recipe reference: the recipe it names, or, when it
 * names none, that it is a reference at all.
 */
export function RecipeRefCell({ line }: { line: CostLine }) {
  const recipe = line.source_recipe ?? null;
  if (recipe) return <RecipeTitleLink recipe={recipe} suffix=" recipe" />;
  return <span className={muted}>Recipe reference</span>;
}

/**
 * A costed line's source: "from Simple syrup recipe" with "1 fl oz of 16.9 fl oz"
 * beneath, "(latest prices)" when a kitchen's view or this week's offers costed
 * the recipe under Latest, the posted mark, and "incomplete" for a partial one.
 */
export function SourceCell({ line }: { line: CostLine }) {
  const recipe = line.source_recipe;
  if (!recipe) return null;
  const makes = makesShort(line.source?.makes);
  return (
    <span className="flex flex-col gap-0.5" data-testid="source-cell">
      <span className="flex flex-wrap items-center gap-x-2">
        <span>
          from <RecipeTitleLink recipe={recipe} suffix=" recipe" />
          {line.source_basis === "latest" ? <span className={muted}> (latest prices)</span> : null}
        </span>
        {line.status === "posted" ? <PostedBadge /> : null}
      </span>
      {makes ? (
        <span className={`text-xs ${muted}`}>
          {lineAmount(line)} of {makes}
        </span>
      ) : null}
      {line.source_partial ? (
        <span className="text-xs text-amber-900 dark:text-amber-200">
          {recipe.title} recipe incomplete
        </span>
      ) : null}
    </span>
  );
}

/** A cycle as title links: "Comet dip → Comet glaze → Comet dip", at most 4 titles. */
function CycleChain({ line }: { line: CostLine }) {
  const hops = line.source?.cycle ?? [];
  const shown = hops.slice(0, 4);
  const more = hops.length - shown.length;
  return (
    <span className="flex flex-col gap-1" data-testid="cycle-chain">
      <span>These recipes cost from each other, so neither can be costed:</span>
      <span className="flex flex-wrap items-center gap-x-1 break-words">
        {shown.map((hop, i) => (
          <Fragment key={`${hop.recipe.id}-${i}`}>
            {i > 0 ? (
              <>
                <span aria-hidden="true">→</span>
                <span className="sr-only">uses</span>
              </>
            ) : null}
            <RecipeTitleLink recipe={hop.recipe} />
          </Fragment>
        ))}
        {more > 0 ? <span>+{more} more</span> : null}
      </span>
      <span className="flex flex-col gap-0.5 text-xs">
        {hops.slice(0, -1).map((hop, i) =>
          hop.via_ingredient ? (
            <span key={`via-${hop.recipe.id}-${i}`}>
              {hop.recipe.title} uses{" "}
              <Link
                to={`/catalog/ingredients/${hop.via_ingredient.id}#made-by-heading`}
                className={link}
              >
                {hop.via_ingredient.name}
              </Link>
              , which {hops[i + 1]?.recipe.title ?? "the next recipe"} makes
            </span>
          ) : (
            <span key={`file-${hop.recipe.id}-${i}`}>
              {hop.recipe.title}: edit <Code>{hop.recipe.path}</Code>
            </span>
          ),
        )}
      </span>
    </span>
  );
}

/** "No recipe at …", with "Did you mean …?" when a file of that name is elsewhere. */
function NoRecipe({ line, recipePath }: { line: CostLine; recipePath: string }) {
  const recipe = line.source_recipe;
  const written = line.line.ref_path;
  if (recipe) {
    // Route (b): the ingredient's recipe file is gone.
    return (
      <span>
        <Code>{recipe.path}</Code> is gone, so {recipe.title} can&apos;t be costed. Restore it, or
        link{" "}
        {line.line.ingredient_id ? (
          <Link
            to={`/catalog/ingredients/${line.line.ingredient_id}#made-by-heading`}
            className={link}
          >
            another recipe
          </Link>
        ) : (
          "another recipe"
        )}
        , then rescan.
      </span>
    );
  }
  const target = line.source?.target_path;
  const suggestion = line.source?.suggestion;
  return (
    <span className="flex flex-col gap-0.5">
      <span>
        {target ? (
          <>
            No recipe at <Code>{target}</Code> (written <Code>{written}</Code> in{" "}
            <Code>{recipePath}</Code>). Create it or fix the path, then rescan.
          </>
        ) : (
          <>
            <Code>{written}</Code> in <Code>{recipePath}</Code> points outside the recipes
            directory. Fix the path, then rescan.
          </>
        )}
      </span>
      {suggestion ? (
        <span>
          Did you mean{" "}
          <Link to={recipeHref(suggestion)} className={link}>
            {suggestion.path}
          </Link>
          ?
        </span>
      ) : null}
    </span>
  );
}

interface WordsContext {
  line: CostLine;
  recipePath: string;
  /** The recipe as a link, or the line's name when there is none. */
  who: ReactNode;
  path: ReactNode;
}

const VOLUME_UNITS = new Set(["ml", "l", "fl_oz", "cup", "tsp", "tbsp", "pt", "qt", "gal"]);

/** One renderer per failure code: the table in the Wave 3a plan, Part 4. */
const WORDS: Partial<Record<string, (c: WordsContext) => ReactNode>> = {
  no_yield: ({ who, path }) => (
    <>
      Add how much {who} makes, <Code>{YIELD_EXAMPLE}</Code>, to {path}, then rescan
    </>
  ),
  bad_yield: ({ line, who, path }) => (
    <>
      {who} makes <Code>{line.source?.makes?.text ?? ""}</Code>, not an amount and a unit. Write it
      as <Code>{YIELD_EXAMPLE}</Code> in {path}, then rescan
    </>
  ),
  yield_unit: ({ line, who }) => {
    const makes = makesShort(line.source?.makes) ?? "";
    const unit = line.line.unit_text ?? line.line.unit ?? "";
    const liquid = line.line.unit === "oz" && VOLUME_UNITS.has(line.source?.makes?.unit ?? "");
    return (
      <>
        {who} makes {makes}; this line is in {unit}.{liquid ? " Write fl oz for a liquid." : ""}
      </>
    );
  },
  no_recipe: ({ line, recipePath }) => <NoRecipe line={line} recipePath={recipePath} />,
  cycle: ({ line }) => <CycleChain line={line} />,
  recipe_unpriced: ({ line, who }) => {
    const counts = line.source_counts;
    return (
      <>
        {who} has no priced lines yet{counts ? ` (${counts.priced} of ${counts.total})` : ""}
      </>
    );
  },
  recipe_unreadable: ({ who, path }) => (
    <>
      {who} can&apos;t be read; fix {path}, then rescan
    </>
  ),
};

/**
 * Why a line from a recipe has no cost, by its failure code. `conversionWords`
 * renders the conversion's own words for a route (b) line whose code is a
 * conversion's, such as `no_density`, with the recipe's way out after them.
 */
export function SourceWords({
  line,
  recipePath,
  conversionWords,
}: {
  line: CostLine;
  recipePath: string;
  conversionWords: ReactNode;
}) {
  const recipe = line.source_recipe ?? null;
  const who = recipe ? <RecipeTitleLink recipe={recipe} /> : line.line.raw_name;
  const path = recipe ? <Code>{recipe.path}</Code> : null;
  const render = WORDS[line.failure_code ?? ""];
  return (
    <span className={`flex flex-col gap-1 ${muted}`} data-testid="source-words">
      {render ? (
        render({ line, recipePath, who, path })
      ) : (
        <>
          {conversionWords}
          {recipe ? (
            <span>
              or give how much <RecipeTitleLink recipe={recipe} /> makes as a volume
            </span>
          ) : null}
        </>
      )}
    </span>
  );
}

/**
 * "1 line costs from Simple syrup recipe, which uses a posted price." Recipe-sourced
 * posted lines are said apart from the lines on a store's price; null when none.
 */
export function sourcedPostedSentence(lines: CostLine[]): string | null {
  const sourced = lines.filter((l) => l.status === "posted" && l.source_recipe);
  if (sourced.length === 0) return null;
  const titles = [...new Set(sourced.map((l) => l.source_recipe?.title ?? ""))];
  if (sourced.length === 1)
    return `1 line costs from ${titles[0]} recipe, which uses a posted price.`;
  return titles.length === 1
    ? `${sourced.length} lines cost from ${titles[0]} recipe, which uses a posted price.`
    : `${sourced.length} lines cost from recipes that use a posted price.`;
}
