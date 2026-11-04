import {
  formatAmount,
  lineText,
  type BodyItem,
  type BodyReference,
  type BodySection,
  type Recipe,
  type RecipeIngredient,
} from "../../api/recipes";
import { focusRing, sectionHeading } from "../ui";

const muted = "text-neutral-600 dark:text-neutral-400";

/** Front matter keys shown first, in this order; every other key follows as "key: value". */
const LEADING_KEYS = ["tags", "source"];
/** Keys the page header already shows: the title, and servings as "Serves 6". */
const HEADER_KEYS = ["title", "servings"];

// An address runs to the next space; commas are valid inside one ("rice,beans").
const URL_PATTERN = /(https?:\/\/\S+)/;
// Prose punctuation after an address belongs to the sentence, not the link.
const TRAILING = /[.,;:!?)\]]+$/;

/** A value with each web address as a link named by its site: "onceuponachef.com". */
function MetaValue({ value }: { value: unknown }) {
  const text = metaValue(value);
  const parts = text.split(URL_PATTERN);
  if (parts.length === 1) return <>{text}</>;
  return (
    <>
      {parts.map((part, i) => {
        if (i % 2 === 0) return part.replace(/,\s*$/, "");
        const href = part.replace(TRAILING, "");
        const trail = part.slice(href.length).replace(/^,\s*$/, "");
        let site = href;
        try {
          site = new URL(href).hostname.replace(/^www\./, "");
        } catch {
          // not a URL after all: shown as written
        }
        return (
          <span key={i}>
            {i > 1 || parts[0].trim() ? " · " : null}
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className={`inline-flex min-h-11 items-center rounded-sm underline underline-offset-2 lg:min-h-0 ${focusRing}`}
            >
              {site}
            </a>
            {trail}
          </span>
        );
      })}
    </>
  );
}

function metaValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  // A Cooklang amount ("6%balls") reads as words: "6 balls".
  return String(value).replace(/^(\d+(?:[./]\d+)?)%(\S.*)$/, "$1 $2");
}

/** The ingredient lines grouped by section, in document order; an unsectioned run comes first. */
export function sections(
  lines: RecipeIngredient[],
): { title: string | null; lines: RecipeIngredient[] }[] {
  const out: { title: string | null; lines: RecipeIngredient[] }[] = [];
  for (const line of [...lines].sort((a, b) => a.seq - b.seq)) {
    const last = out.at(-1);
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
 * 0048) shows its ingredient lines by section instead, until the next scan.
 * Read-only: the words say where to edit.
 */
export function RenderedRecipe({ recipe, id }: { recipe: Recipe; id?: string }) {
  const meta = recipe.front_matter ?? {};
  const keys = [
    ...LEADING_KEYS.filter((k) => k in meta),
    ...Object.keys(meta).filter((k) => !LEADING_KEYS.includes(k) && !HEADER_KEYS.includes(k)),
  ];
  return (
    <section
      id={id}
      aria-labelledby={`${id ?? "recipe"}-heading`}
      className="flex min-w-0 flex-col gap-4"
    >
      <h2 id={`${id ?? "recipe"}-heading`} className={sectionHeading}>
        Recipe
      </h2>
      {keys.length > 0 ? (
        <dl
          className={`grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm ${muted}`}
          aria-label="Front matter"
        >
          {keys.map((k) => (
            <div key={k} className="contents">
              <dt className="font-medium">{k}</dt>
              <dd className="min-w-0 break-words">
                <MetaValue value={meta[k]} />
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {recipe.body ? (
        <Steps body={recipe.body} prefix={id ?? "recipe"} />
      ) : (
        <IngredientLines lines={recipe.ingredients} />
      )}
      <p className={`text-xs ${muted}`}>
        Edit this recipe in its file; it updates here on the next scan.
      </p>
    </section>
  );
}

/** The anchor of a section, or of one of its steps, for a reference to link to. */
function anchor(prefix: string, section: number, step?: number | null): string {
  return step === null || step === undefined
    ? `${prefix}-s${section + 1}`
    : `${prefix}-s${section + 1}-${step + 1}`;
}

const targetHighlight = "scroll-mt-4 rounded-sm target:bg-neutral-100 dark:target:bg-neutral-800";

/** A step that is a `>` note, with its words. */
function noteOf(step: BodyItem[]): string | null {
  const [first] = step;
  return step.length === 1 && first.t === "note" ? first.v : null;
}

/**
 * A section's steps in runs: numbered steps in an `ol` that continues its count
 * across notes, and each note as an aside between them, never numbered.
 */
function runs(
  steps: BodyItem[][],
): ({ note: string; at: number } | { start: number; at: number[] })[] {
  const out: ({ note: string; at: number } | { start: number; at: number[] })[] = [];
  let number = 1;
  steps.forEach((step, j) => {
    const note = noteOf(step);
    if (note !== null) {
      out.push({ note, at: j });
      return;
    }
    const last = out.at(-1);
    if (last && "start" in last) last.at.push(j);
    else out.push({ start: number, at: [j] });
    number += 1;
  });
  return out;
}

/** The number a step is shown with, counting only steps that are not notes. */
export function stepNumber(steps: BodyItem[][], index: number): number {
  return steps.slice(0, index + 1).filter((s) => noteOf(s) === null).length;
}

/** The steps as written: numbered within each section, items inline, notes between. */
function Steps({ body, prefix }: { body: BodySection[]; prefix: string }) {
  if (!body.some((s) => s.steps.length > 0))
    return <p className={`text-sm ${muted}`}>This recipe has no steps.</p>;
  return (
    <>
      {body.map((section, i) =>
        section.steps.length === 0 ? null : (
          <div
            key={`${section.name ?? ""}-${i}`}
            id={anchor(prefix, i)}
            className={`flex flex-col gap-2 ${targetHighlight}`}
            data-testid="recipe-section"
          >
            {section.name ? <h2 className="text-base font-medium">{section.name}</h2> : null}
            {runs(section.steps).map((run) =>
              "note" in run ? (
                <p
                  key={`n${run.at}`}
                  id={anchor(prefix, i, run.at)}
                  className={`border-l-2 border-neutral-300 pl-3 text-sm leading-6 dark:border-neutral-700 ${muted} ${targetHighlight}`}
                  data-testid="recipe-note"
                >
                  {run.note}
                </p>
              ) : (
                <ol
                  key={`s${run.at[0]}`}
                  start={run.start}
                  className="flex list-decimal flex-col gap-2 pl-6 text-sm leading-6"
                  aria-label={section.name ? `${section.name} steps` : "Steps"}
                >
                  {run.at.map((j) => (
                    <li key={j} id={anchor(prefix, i, j)} className={targetHighlight}>
                      {section.steps[j].map((item, k) => (
                        <Item key={k} item={item} body={body} prefix={prefix} section={i} />
                      ))}
                    </li>
                  ))}
                </ol>
              ),
            )}
          </div>
        ),
      )}
    </>
  );
}

/** "2 cups", "1–2 tsp", "1/3 cup", "a handful", or "" — the quantity words for an item. */
function quantity(qty: string | null, unit?: string | null): string {
  return [qty ? amount(qty) : qty, unit].filter((p) => p).join(" ");
}

/** A body quantity for reading: each number of "0.333…" or "1–2" through `formatAmount`. */
function amount(qty: string): string {
  return qty
    .split("–")
    .map((part) => (/^\d+(\.\d+)?$/.test(part.trim()) ? formatAmount(part.trim()) : part))
    .join("–");
}

/** Where a reference came from, in words: "Sauce", "step 2", "step 1 of Preparation". */
function origin(ref: BodyReference, body: BodySection[], here: string | null): string {
  const name = body[ref.section]?.name ?? null;
  const section = name ?? `section ${ref.section + 1}`;
  if (ref.step === null) return section;
  const step = `step ${stepNumber(body[ref.section]?.steps ?? [], ref.step)}`;
  return name === here ? step : `${step} of ${section}`;
}

/** A reference: its name links to the step or section it came from; it has no line to cost. */
function ReferenceItem({ item, body, prefix, section }: ItemProps & { item: BodyReference }) {
  const words = quantity(item.qty, item.unit);
  const here = body[section]?.name ?? null;
  return (
    <span data-testid="step-reference">
      <a
        href={`#${anchor(prefix, item.section, item.step)}`}
        className={`rounded-sm font-semibold underline underline-offset-2 ${focusRing}`}
      >
        {item.name}
        <span className="sr-only"> (from {origin(item, body, here)})</span>
      </a>
      {words ? <span className={muted}> {words}</span> : null}
      {item.note ? <span className={muted}> ({item.note})</span> : null}
    </span>
  );
}

/** Where an item sits: the whole body, the anchor prefix, and its own section's index. */
interface ItemProps {
  body: BodySection[];
  prefix: string;
  section: number;
}

function Item({ item, ...where }: ItemProps & { item: BodyItem }) {
  switch (item.t) {
    case "text":
    case "note":
      return <>{item.v}</>;
    case "reference":
      return <ReferenceItem item={item} {...where} />;
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
  if (groups.length === 0)
    return <p className={`text-sm ${muted}`}>This recipe names no ingredients.</p>;
  return (
    <>
      {groups.map((g, i) => (
        <div key={`${g.title ?? ""}-${i}`} className="flex flex-col gap-2">
          {g.title ? <h2 className="text-base font-medium">{g.title}</h2> : null}
          <ol
            className="flex flex-col gap-1 text-sm"
            aria-label={g.title ? `${g.title} ingredients` : "Ingredients"}
          >
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
  return whole.endsWith(line.raw_name)
    ? whole.slice(0, whole.length - line.raw_name.length).trim()
    : whole;
}
