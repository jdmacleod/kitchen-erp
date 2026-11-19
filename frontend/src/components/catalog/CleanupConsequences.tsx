// What a move or a unit switch in Tidy the catalog changes beyond its own row
// (issues 525 and 527): the consequences list, the accept button's words and the
// Notice an accept ends with.

import { useState } from "react";
import { Link } from "react-router";
import type {
  CleanupConsequence,
  CleanupResult,
  CleanupSuggestion,
} from "../../api/catalogCleanup";
import { Button, focusRing } from "../ui";

const muted = "text-neutral-600 dark:text-neutral-400";
const SHOWN = 5;

/** The consequences as a list, recipe lines linked to their recipe; five, then "Show all N". */
export function Consequences({ items }: { items: CleanupConsequence[] }) {
  const [all, setAll] = useState(false);
  if (items.length === 0) return null;
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <>
      <ul className={`list-disc pl-5 text-xs ${muted}`} aria-label="What else this changes">
        {shown.map((c, i) => (
          <li key={`${c.text}-${i}`}>
            {c.recipe_id ? (
              <Link to={`/cook/recipes/${c.recipe_id}`} className={`underline ${focusRing}`}>
                {c.text}
              </Link>
            ) : (
              c.text
            )}
          </li>
        ))}
      </ul>
      {items.length > SHOWN && !all ? (
        <Button variant="ghost" className="self-start text-xs" onClick={() => setAll(true)}>
          Show all {items.length}
        </Button>
      ) : null}
    </>
  );
}

/** "Move to bourbon", "Switch to ml", or "Accept". */
export function acceptLabel(s: CleanupSuggestion): string {
  if (s.kind === "product_move") return `Move to ${s.after}`;
  if (s.kind === "unit_mismatch") return `Switch to ${s.after}`;
  return "Accept";
}

const counted = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "Moved to bourbon: 3 recipes recomputed"; "Switched to ml: 12 prices renormalized, …". */
export function resultSentence(s: CleanupSuggestion, r: CleanupResult): string {
  const recipes = counted(r.recipes_recomputed, "recipe", "recipes") + " recomputed";
  if (s.kind === "product_move") return `Moved to ${s.after}: ${recipes}`;
  const parts = [
    `${counted(r.prices_renormalized, "price", "prices")} renormalized`,
    recipes,
    r.measures_rewritten ? `${counted(r.measures_rewritten, "measure", "measures")} rewritten` : "",
    r.measures_dropped ? `${counted(r.measures_dropped, "measure", "measures")} dropped` : "",
  ].filter(Boolean);
  return `Switched to ${s.after}: ${parts.join(", ")}`;
}
