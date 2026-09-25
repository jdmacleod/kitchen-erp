/**
 * Colours for drawing surfaces that cannot read CSS: the MapLibre basemap and the
 * SVG price chart. MapLibre parses only sRGB colours, so these are hex mirrors of
 * OKLCH values. `palette.test.ts` recomputes each one from its OKLCH source and fails
 * if they drift apart. Everything else in the app takes colour from the Tailwind
 * scales that theme.css remaps (docs/spec/08-ui-design-system.md).
 */

/**
 * OKLCH sources, written as theme.css writes them: "lightness chroma hue".
 * Neutrals match theme.css exactly.
 */
export const OKLCH = {
  "neutral-50": "0.97 0.013 84",
  "neutral-100": "0.945 0.019 82",
  "neutral-200": "0.905 0.024 78",
  "neutral-300": "0.845 0.028 74",
  "neutral-700": "0.395 0.028 55",
  "neutral-800": "0.31 0.024 54",
  "neutral-900": "0.245 0.02 52",
  "neutral-950": "0.195 0.016 52",
  // Muted tide blue and leaf for water and parks: desaturated so the pins stay loudest.
  "water-light": "0.87 0.03 225",
  "water-dark": "0.3 0.03 225",
  "park-light": "0.935 0.025 128",
  "park-dark": "0.265 0.02 128",
  // Chart series: six hues at matched lightness and chroma. Each is at least 3.5:1
  // against both the light and the dark card, above the 3:1 floor for graphics.
  "series-1": "0.6 0.12 255",
  "series-2": "0.6 0.12 40",
  "series-3": "0.6 0.12 140",
  "series-4": "0.6 0.12 305",
  "series-5": "0.6 0.12 85",
  "series-6": "0.6 0.12 195",
} as const satisfies Record<string, string>;

export type PaletteKey = keyof typeof OKLCH;

export const HEX: Record<PaletteKey, string> = {
  "neutral-50": "#F9F5EC",
  "neutral-100": "#F3ECDF",
  "neutral-200": "#E9DECE",
  "neutral-300": "#D7CAB9",
  "neutral-700": "#534338",
  "neutral-800": "#3A2D25",
  "neutral-900": "#281E18",
  "neutral-950": "#1B130E",
  "water-light": "#C0D9E3",
  "water-dark": "#1D3139",
  "park-light": "#E5EDDB",
  "park-dark": "#22271D",
  "series-1": "#4C82C6",
  "series-2": "#BB6546",
  "series-3": "#57914A",
  "series-4": "#916CB9",
  "series-5": "#A1790C",
  "series-6": "#009696",
};

/** The price chart's series colours; each series also has its own dash and marker. */
export const SERIES_COLOURS = [
  HEX["series-1"],
  HEX["series-2"],
  HEX["series-3"],
  HEX["series-4"],
  HEX["series-5"],
  HEX["series-6"],
] as const;

/** Basemap colours on the cream (light) and espresso (dark) grounds. */
export function basemapColours(dark: boolean) {
  const ground = dark ? HEX["neutral-950"] : HEX["neutral-50"];
  const park = dark ? HEX["park-dark"] : HEX["park-light"];
  const minor = dark ? HEX["neutral-800"] : HEX["neutral-200"];
  const major = dark ? HEX["neutral-700"] : HEX["neutral-300"];
  return {
    background: ground,
    earth: ground,
    water: dark ? HEX["water-dark"] : HEX["water-light"],
    park_a: park,
    park_b: park,
    wood_a: park,
    wood_b: park,
    scrub_a: park,
    scrub_b: park,
    buildings: dark ? HEX["neutral-900"] : HEX["neutral-100"],
    other: minor,
    minor_service: minor,
    minor_a: minor,
    minor_b: minor,
    link: major,
    major,
    highway: major,
  };
}

/** The three numbers of an "L C H" string. */
export function parseOklch(value: string): [number, number, number] {
  const [l, c, h] = value.split(" ").map(Number);
  return [l, c, h];
}

/** sRGB hex for an OKLCH colour, clamped to the gamut. Used by the drift test. */
export function oklchToHex(l: number, c: number, h: number): string {
  const a = c * Math.cos((h * Math.PI) / 180);
  const b = c * Math.sin((h * Math.PI) / 180);
  const l_ = l + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = l - 0.0894841775 * a - 1.291485548 * b;
  const [L, M, S] = [l_ ** 3, m_ ** 3, s_ ** 3];
  const linear = [
    4.0767416621 * L - 3.3077115913 * M + 0.2309699292 * S,
    -1.2684380046 * L + 2.6097574011 * M - 0.3413193965 * S,
    -0.0041960863 * L - 0.7034186147 * M + 1.707614701 * S,
  ];
  return (
    "#" +
    linear
      .map((x) => {
        const v = Math.min(1, Math.max(0, x));
        const encoded = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
        return Math.round(encoded * 255)
          .toString(16)
          .padStart(2, "0")
          .toUpperCase();
      })
      .join("")
  );
}
