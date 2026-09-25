import { describe, expect, it } from "vitest";
import { purchaseStatusTone } from "../api/purchases";
import { HEX, OKLCH, oklchToHex, parseOklch, type PaletteKey } from "../lib/palette";
import themeCss from "../theme.css?raw";

/** Every `--color-<scale>-<shade>` theme.css defines, with its OKLCH triple. */
const defined = new Map<string, [number, number, number]>(
  [...themeCss.matchAll(/--color-([a-z]+-\d+): oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)/g)].map((m) => [
    m[1],
    [Number(m[2]), Number(m[3]), Number(m[4])],
  ]),
);

/** The app's own source, read raw; tests are excluded. */
const raw = import.meta.glob<string>(["../**/*.{ts,tsx,css}", "!../test/**"], {
  query: "?raw",
  import: "default",
  eager: true,
});
const files = Object.entries(raw).map(([key, text]) => ({ path: key.replace(/^\.\.\//, ""), text }));

describe("theme coverage (spec 08)", () => {
  it("defines every shade of the five scales the app uses", () => {
    // A shade theme.css leaves out falls back to stock Tailwind and breaks the palette.
    const missing = new Set<string>();
    for (const { path, text } of files) {
      if (path === "theme.css") continue;
      for (const m of text.matchAll(/\b(?:[a-z-]+:)*[a-z]+-(neutral|blue|red|amber|green)-(\d{2,3})\b/g)) {
        const key = `${m[1]}-${m[2]}`;
        if (!defined.has(key)) missing.add(`${key} (${path})`);
      }
    }
    expect([...missing]).toEqual([]);
  });

  it("uses no Tailwind colour scale outside the five remapped ones", () => {
    const other =
      /\b(?:bg|text|border|ring|outline|fill|stroke|divide|accent|caret|decoration|from|to|via|placeholder)-(?:slate|gray|zinc|stone|orange|yellow|lime|emerald|teal|cyan|sky|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b/;
    const offenders = files.filter(({ text }) => other.test(text)).map(({ path }) => path);
    expect(offenders).toEqual([]);
  });

  it("keeps raw hex colours out of the app except the palette mirrors", () => {
    const offenders = files
      .filter(({ path }) => path !== "theme.css" && path !== "lib/palette.ts")
      .filter(({ text }) => /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])/.test(text))
      .map(({ path }) => path);
    expect(offenders).toEqual([]);
  });

  it("keeps blue for actions: no info or selection surface is tinted blue", () => {
    // Herb (the remapped blue) means "you can act on this". Tints like bg-blue-50
    // read as a button, so they belong nowhere; blue fills are for primary actions.
    const tinted = files
      .filter(({ path }) => path !== "theme.css")
      .filter(({ text }) => /\bbg-blue-(?:50|200|300|950)\b/.test(text))
      .map(({ path }) => path);
    expect(tinted).toEqual([]);
  });
});

describe("palette mirrors (lib/palette.ts)", () => {
  it("matches each hex mirror to its OKLCH source", () => {
    for (const key of Object.keys(OKLCH) as PaletteKey[]) {
      const [l, c, h] = parseOklch(OKLCH[key]);
      expect(`${key} ${HEX[key]}`).toBe(`${key} ${oklchToHex(l, c, h)}`);
    }
  });

  it("uses theme.css's own values for the neutrals", () => {
    for (const key of Object.keys(OKLCH).filter((k) => k.startsWith("neutral-")) as PaletteKey[]) {
      expect(`${key} ${parseOklch(OKLCH[key]).join(" ")}`).toBe(`${key} ${defined.get(key)?.join(" ")}`);
    }
  });
});

describe("status tones (spec 08)", () => {
  it("gives each purchase status its own meaning", () => {
    // Draft waits on a person (squash), Reviewed is neutral (T14), Committed is done (olive).
    expect(purchaseStatusTone).toEqual({ draft: "warn", reviewed: "neutral", committed: "good" });
  });
});
