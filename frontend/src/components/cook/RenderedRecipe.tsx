import { lineText, type Recipe, type RecipeIngredient } from "../../api/recipes";

const muted = "text-neutral-600 dark:text-neutral-400";

/** Front matter keys shown first, in this order; every other key follows as "key: value". */
const LEADING_KEYS = ["servings", "tags", "source"];

function metaValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** The ingredient lines grouped by section, in document order; an unsectioned run comes first. */
export function sections(lines: RecipeIngredient[]): { title: string | null; lines: RecipeIngredient[] }[] {
  const out: { title: string | null; lines: RecipeIngredient[] }[] = [];
  for (const line of [...lines].sort((a, b) => a.seq - b.seq)) {
    const last = out[out.length - 1];
    if (last && last.title === line.section) last.lines.push(line);
    else out.push({ title: line.section, lines: [line] });
  }
  return out;
}

/**
 * The file's structure as the index holds it (10, Rendered recipe): the front
 * matter as a meta block, sections as h2, and each ingredient line as written,
 * with its name in weight 600, its quantity in neutral and its note in
 * parentheses. The index keeps ingredient rows, not step text, so the steps
 * themselves are not here. Read-only: the words say where to edit.
 */
export function RenderedRecipe({ recipe, id }: { recipe: Recipe; id?: string }) {
  const meta = recipe.front_matter ?? {};
  const keys = [...LEADING_KEYS.filter((k) => k in meta && k !== "title"), ...Object.keys(meta).filter((k) => !LEADING_KEYS.includes(k) && k !== "title")];
  const groups = sections(recipe.ingredients);
  return (
    <section id={id} aria-labelledby={`${id ?? "recipe"}-heading`} className="flex min-w-0 flex-col gap-4">
      <h2 id={`${id ?? "recipe"}-heading`} className="text-lg font-medium">
        Recipe
      </h2>
      {keys.length > 0 ? (
        <dl className={`grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm ${muted}`} aria-label="Front matter">
          {keys.map((k) => (
            <div key={k} className="contents">
              <dt className="font-medium">{k}</dt>
              <dd className="min-w-0 break-words">{metaValue(meta[k])}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {groups.length === 0 ? (
        <p className={`text-sm ${muted}`}>This recipe names no ingredients.</p>
      ) : (
        groups.map((g, i) => (
          <div key={`${g.title ?? ""}-${i}`} className="flex flex-col gap-2">
            {g.title ? <h2 className="text-base font-medium">{g.title}</h2> : null}
            <ol className="flex flex-col gap-1 text-sm" aria-label={g.title ? `${g.title} ingredients` : "Ingredients"}>
              {g.lines.map((line) => (
                <li key={line.id} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-semibold">{line.raw_name}</span>
                  <span className={muted}>{quantityWords(line)}</span>
                  {line.note ? <span className={muted}>({line.note})</span> : null}
                </li>
              ))}
            </ol>
          </div>
        ))
      )}
      <p className={`text-xs ${muted}`}>Edit this recipe in its file; it updates here on the next scan.</p>
    </section>
  );
}

/** The quantity and unit as written, without the name: "2 cups". */
function quantityWords(line: RecipeIngredient): string {
  const whole = lineText({ ...line, note: null });
  return whole.endsWith(line.raw_name) ? whole.slice(0, whole.length - line.raw_name.length).trim() : whole;
}
