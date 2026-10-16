/**
 * What may carry each colour on a page (08, rule 2; UI-7.19, UI-8.20). Blue is
 * an action's alone, so it is allowed only on a link or a button or inside one;
 * every other scale is allowed where the page says it means something.
 */
export interface ColourMeanings {
  /** Olive: cheapest, done, success. */
  olive?: (el: HTMLElement, text: string) => boolean;
  /** Squash: stale, estimated, unsourceable and their hints. */
  squash?: (el: HTMLElement, text: string) => boolean;
  /** Tomato: a destructive action or an error. */
  tomato?: (el: HTMLElement, text: string) => boolean;
}

const never = () => false;

/** The remapped Tailwind scale behind each colour's name (`theme.css`). */
const SCALES: Record<string, string> = {
  blue: "blue",
  olive: "green",
  squash: "amber",
  tomato: "red",
};

function hasScale(cls: string, scale: string): boolean {
  // The colour utilities, with or without the dark variant; not focus rings or
  // hover states, which only an action has.
  return new RegExp(`(^|\\s)(dark:)?(bg|text|border|fill|stroke)-${scale}-`).test(cls);
}

/**
 * Walks every element under `root` and describes each whose own classes carry a
 * colour outside its meaning. An empty list means nothing is coloured that
 * should not be.
 */
export function colourOutsideItsMeaning(root: HTMLElement, meanings: ColourMeanings): string[] {
  const { olive = never, squash = never, tomato = never } = meanings;
  const found: string[] = [];
  for (const el of root.querySelectorAll<HTMLElement>("*")) {
    const cls = el.getAttribute("class") ?? "";
    if (!cls) continue;
    const text = el.textContent.trim();
    const describe = `<${el.tagName.toLowerCase()} class="${cls}"> "${text.slice(0, 40)}"`;
    const allowed: Record<string, boolean> = {
      blue: el.closest("a, button") !== null,
      olive: olive(el, text),
      squash: squash(el, text),
      tomato: tomato(el, text),
    };
    for (const [name, scale] of Object.entries(SCALES)) {
      if (hasScale(cls, scale) && !allowed[name]) found.push(`${name}: ${describe}`);
    }
  }
  return found;
}
