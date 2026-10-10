import { lineText, type BodyItem, type BodySection, type Recipe, type RecipeIngredient } from "../../api/recipes";

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
 * The file's structure (10, Rendered recipe): the front matter as a meta block,
 * sections as h2, and the steps numbered, with ingredient references in weight
 * 600 and their quantity, cookware and timers in neutral, and an ingredient's
 * note in parentheses. A recipe indexed before the body was kept (migration
 * 0047) shows its ingredient lines by section instead, until the next scan.
 * Read-only: the words say where to edit.
 */
export function RenderedRecipe({ recipe, id }: { recipe: Recipe; id?: string }) {
  const meta = recipe.front_matter ?? {};
  const keys = [...LEADING_KEYS.filter((k) => k in meta && k !== "title"), ...Object.keys(meta).filter((k) => !LEADING_KEYS.includes(k) && k !== "title")];
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
      {recipe.body ? <Steps body={recipe.body} /> : <IngredientLines lines={recipe.ingredients} />}
      <p className={`text-xs ${muted}`}>Edit this recipe in its file; it updates here on the next scan.</p>
    </section>
  );
}

/** The steps as written: numbered within each section, items inline. */
function Steps({ body }: { body: BodySection[] }) {
  const withSteps = body.filter((s) => s.steps.length > 0);
  if (withSteps.length === 0) return <p className={`text-sm ${muted}`}>This recipe has no steps.</p>;
  return (
    <>
      {withSteps.map((section, i) => (
        <div key={`${section.name ?? ""}-${i}`} className="flex flex-col gap-2" data-testid="recipe-section">
          {section.name ? <h2 className="text-base font-medium">{section.name}</h2> : null}
          <ol className="flex list-decimal flex-col gap-2 pl-6 text-sm leading-6" aria-label={section.name ? `${section.name} steps` : "Steps"}>
            {section.steps.map((step, j) => (
              <li key={j}>
                {step.map((item, k) => (
                  <Item key={k} item={item} />
                ))}
              </li>
            ))}
          </ol>
        </div>
      ))}
    </>
  );
}

/** "2 cups", "1–2 tsp", "a handful", or "" — the quantity words for an item. */
function quantity(qty: string | null, unit?: string | null): string {
  return [qty, unit].filter((p) => p).join(" ");
}

function Item({ item }: { item: BodyItem }) {
  switch (item.t) {
    case "text":
      return <>{item.v}</>;
    case "ingredient": {
      const words = quantity(item.qty, item.unit);
      return (
        <span data-testid="step-ingredient" data-seq={item.seq}>
          <span className="font-semibold">{item.name}</span>
          {words ? <span className={muted}> {words}</span> : null}
          {item.note ? <span className={muted}> ({item.note})</span> : null}
        </span>
      );
    }
    case "cookware": {
      const words = quantity(item.qty);
      return (
        <span className={muted} data-testid="step-cookware">
          {item.name}
          {words ? ` ${words}` : ""}
        </span>
      );
    }
    case "timer": {
      const words = quantity(item.qty, item.unit);
      return (
        <span className={muted} data-testid="step-timer">
          {item.name ? `${item.name} ` : ""}
          {words}
        </span>
      );
    }
  }
}

/** The older rendering: ingredient lines by section, for an index without a body. */
function IngredientLines({ lines }: { lines: RecipeIngredient[] }) {
  const groups = sections(lines);
  if (groups.length === 0) return <p className={`text-sm ${muted}`}>This recipe names no ingredients.</p>;
  return (
    <>
      {groups.map((g, i) => (
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
      ))}
    </>
  );
}

/** The quantity and unit as written, without the name: "2 cups". */
function quantityWords(line: RecipeIngredient): string {
  const whole = lineText({ ...line, note: null });
  return whole.endsWith(line.raw_name) ? whole.slice(0, whole.length - line.raw_name.length).trim() : whole;
}
