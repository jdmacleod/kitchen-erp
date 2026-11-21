// What a recipe makes and who uses it, on the recipe page (issue 526): the
// header's "Makes 16.9 fl oz (500 ml)", the hint when a used recipe's `yield:`
// can't be read, and the "Used in" list. The UI says "makes", never "yield".

import { Link } from "react-router";
import type { Recipe } from "../../api/recipes";
import { makesText, YIELD_EXAMPLE } from "../../api/recipeSources";
import { Alert, focusRing, sectionHeading, tapTarget } from "../ui";

const muted = "text-neutral-600 dark:text-neutral-400";

/** "Makes 16.9 fl oz (500 ml)"; a `yield:` that isn't an amount and a unit, as written. */
export function MakesMeta({ recipe }: { recipe: Recipe }) {
  const makes = recipe.makes;
  if (!makes?.text) return null;
  return (
    <span data-testid="makes-meta">
      Makes {makes.qty ? makesText(makes) : <code className="font-mono text-xs">{makes.text}</code>}
    </span>
  );
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * For a recipe other recipes cost from: what to write when its `yield:` can't be
 * read, or is missing. A recipe nobody uses stays quiet.
 */
export function YieldHint({ recipe }: { recipe: Recipe }) {
  const users = recipe.used_in?.length ?? 0;
  if (users === 0 || recipe.makes?.qty) return null;
  const lines = recipe.used_lines ?? users;
  const what = recipe.makes?.text ? (
    <>
      <code className="font-mono text-xs">{recipe.makes.text}</code> isn&apos;t an amount and a
      unit, so
    </>
  ) : (
    <>This recipe doesn&apos;t say how much it makes, so</>
  );
  return (
    <Alert tone="warn" className="mb-4">
      <span data-testid="yield-hint">
        {what} {plural(lines, "line")} in {plural(users, "recipe")} can&apos;t be costed. Write it
        as <code className="font-mono text-xs">{YIELD_EXAMPLE}</code> in{" "}
        <code className="font-mono text-xs">{recipe.path}</code>, then rescan.
      </span>
    </Alert>
  );
}

/** The recipes that cost from this one, each once, by title. Hidden when there are none. */
export function UsedIn({ recipe }: { recipe: Recipe }) {
  const used = recipe.used_in ?? [];
  if (used.length === 0) return null;
  return (
    <section aria-labelledby="used-in-heading" className="flex flex-col gap-2" id="used-in">
      <h2 id="used-in-heading" className={sectionHeading}>
        Used in
      </h2>
      <ul className="flex flex-col gap-1 text-sm" data-testid="used-in">
        {used.map((r) => (
          <li key={r.id}>
            <Link
              to={`/cook/recipes/${r.id}`}
              className={`${tapTarget} rounded-sm underline-offset-2 hover:underline ${focusRing}`}
            >
              {r.title}
            </Link>
            {r.status !== "ok" ? (
              <span className={muted}>
                {" "}
                · {r.status === "missing" ? "file gone" : "can't be read"}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
