# 08 — UI design system ("Market")

This document specifies the visual system for the Kitchen ERP web app. It applies to every screen in every phase. The visual reference is the private design canvas at https://claude.ai/artifact/HiZpR3VsDVnU2VkGUreeKt; ask the project owner for screenshots if you cannot open it. Where the canvas and this document disagree, this document wins.

Rows in the canvas marked with brackets, such as `[Brand]`, `[Vendor]` or `[n]`, are placeholders. Other sample rows use the project's invented demo vendors and are illustrative, not seed data.

This document and 09–11 were adopted on 2026-09-25 after CEO, engineering and design reviews. Decision IDs in parentheses (T12, D18, G17 and so on) refer to that review's record; each marks a place where the approved handoff was changed or made more specific.

## Direction

Organic and food-themed, but clean and streamlined. A warm cream ground with walnut text, one herb-green action colour, and produce-coloured accents that carry meaning only for ingredient categories. Softer corners than a typical admin UI, one serif for page titles, and a sans for everything else.

Spend boldness in one place: the category chips are the expressive element. Everything around them stays quiet.

## Implementation strategy

The frontend is React + TypeScript + Vite with Tailwind v4, and components use `neutral-*`, `blue-*`, `red-*`, `amber-*` and `green-*` utilities. Dark mode follows `prefers-color-scheme` through the `dark:` variant.

`frontend/src/theme.css` remaps those scales in a Tailwind `@theme` block. Import it immediately after Tailwind in the main stylesheet, and remove the unlayered `html { font-family }` rule from `index.css`, which would otherwise override the theme's base layer:

```css
@import "tailwindcss";
@import "./theme.css";
```

`theme.css` defines every shade the app uses, including red-300 and red-950, amber-300, amber-400, amber-500 and amber-950, and green-300 and green-950; a shade the theme does not define falls back to stock Tailwind and breaks the palette.

The remap alone is not enough, because some scales carry more than one meaning today (T12). Before the remap lands, a semantic pre-pass changes these class names:

- Blue used for information or selection (the info alert tone, the combobox's active option, the current review row, the USDA and new-product panels) moves to neutral. After the remap, blue means only "you can act on this".
- The badge gains a tone map in one place (`components/catalog/fields.tsx`): cheapest and sale use olive; new, open and adopted use neutral.
- Links and the active navigation item become herb green.
- Raw hex values are removed from `map.css`, `MapView.tsx` and `PriceHistoryChart.tsx`.

Do not introduce a second colour system. New components use the same utility names (`bg-neutral-50`, `bg-blue-600`, and so on); the remap is the single source of truth. Semantic aliases (`--color-surface`, `--color-accent`) may be added later as a refactor, but are not required.

## Color

All values are OKLCH and live in `theme.css`. The roles:

| Scale | Role | Notes |
|---|---|---|
| `neutral-*` | Pantry neutrals, cream → oat → walnut → espresso | Light mode: `neutral-50` page, `white` cards, `neutral-100` sidebar, `neutral-900` text. Dark mode uses the same scale reversed, which yields a warm espresso theme. |
| `blue-*` | Primary action: herb green (hue 150) | Buttons, links, focus rings, active nav. Green means "you can act on this" and nothing else. |
| `red-*` | Tomato | Errors, destructive actions, failed receipt reads. |
| `amber-*` | Squash | Warnings, drafts, counts, and anything the system is guessing (G2). |
| `green-*` | Olive (hue 118) | Success, committed, cheapest, on sale. Deliberately off the herb hue so a success badge never reads as a button. |
| `white` | Warm white | Card and input surfaces. |

Rules:

1. Never use a raw hex or a Tailwind colour outside these five scales in new code. The one exception is the chart series palette below.
2. Status is never conveyed by colour alone. Every badge carries a text label.
3. Text on a tinted fill uses the dark end of the same scale (for example `bg-amber-100 text-amber-800`), and the light end in dark mode.
4. Captions and secondary text use `neutral-600` on light surfaces and `neutral-400` on dark surfaces. `neutral-500` measures 3.8:1 on the dark card surface and fails (D19).

Dark mode was checked on the canvas's three "Dark (espresso)" artboards (D19). Measured on the dark card surface: body text 13.9:1, secondary text 6.4:1, the primary button's label 5.5:1, and every category chip 8.5:1 or better.

### Browser surfaces

Theme the surfaces browsers draw themselves, in both themes (G17): `::selection` in `blue-100` (dark: `blue-900`), `caret-color` and `accent-color` in `blue-600`, and `scrollbar-color` in `neutral-300` on a transparent track (dark: `neutral-700`).

### Chart series

The product price chart draws up to six vendor series, so it may use a six-hue series palette that exists for charts only (T13b). Choose the six hues at matched OKLCH lightness during UI-1, record their values here, and give each series a distinct line style as well, so colour is never the only signal. Series colours never appear outside charts.

## Ingredient category accents

Category colours are the only place produce hues appear. `theme.css` defines each category as a hue and chroma; light and dark tints are derived automatically.

| Key | Hue name | Matches free-text values such as |
|---|---|---|
| `produce` | Leaf | vegetables, veg, fruit, herbs, greens |
| `dairy` | Butter | cheese, eggs, milk |
| `meat` | Beet | poultry, beef, pork, charcuterie |
| `seafood` | Tide | fish, shellfish |
| `bakery` | Crust | bread |
| `pantry` | Grain | dry goods, grains, canned, baking, oils, condiments |
| `spices` | Paprika | spice, seasoning(s) |
| `beverages` | Plum | beverage, drinks, coffee, tea |
| `frozen` | Frost | frozen |
| (none) | Neutral | empty, null, or anything unrecognized |

`ingredient.category` is nullable free text. The backend owns its normalization (D12): a pure module, `backend/app/catalog/categories.py`, maps free text to one of the nine keys through a synonym map. It has unit and Hypothesis tests, and ingredient responses carry the result as `category_key`. The server can then filter by category, and every client agrees on what a category means.

`frontend/src/components/CategoryChip.tsx` renders from the API: it takes `category_key` and the free-text label, and falls back to the neutral chip when the key is null. It has no synonym map of its own. It exports:

- `CategoryChip`, the tinted pill with a coloured dot and the category label.
- `categoryClass(key)`, the class names for any element that should carry the category colour.
- `CATEGORY_KEYS`, for filters.

When category becomes a managed list, use exactly these nine keys so the chip mapping needs no change. Adding a category is two lines in `theme.css` plus one synonym entry in the backend module.

The `.cat-edge` class (a category-coloured leading edge on list rows) exists in `theme.css` but is not used in the approved layouts. Prefer chips.

## Typography

| Role | Face | Where |
|---|---|---|
| Display | Fraunces (variable, `opsz` and `SOFT` axes) | `h1` page titles automatically; section and card headings opt in with the `font-display` utility. |
| Body and data | Inter (variable) | Everything else, including tables, forms and navigation. |

Self-host the fonts; the app is self-hosted and makes no runtime requests to Google Fonts:

```bash
pnpm add @fontsource-variable/fraunces @fontsource-variable/inter
```

Import both in `main.tsx`, latin subset only. The font stacks in `theme.css` already reference `"Fraunces Variable"` and `"Inter Variable"` with system fallbacks. `PageHeader`'s own size and tracking utilities must not override the `h1` rule.

Scale and rules:

- Page title (`h1`): 40px desktop, 30–32px phone, weight 600, tracking −0.015em.
- Section heading: Fraunces 19–21px, weight 600, via `font-display`.
- Body: 14–15px. Secondary text: 13px in `neutral-600` (dark: `neutral-400`). Captions: 12px, never lighter than those.
- Tables, prices, quantities and numeric inputs use tabular figures (set in `theme.css`).
- Sentence case everywhere. No all-caps labels or eyebrows; the current `uppercase tracking-wide` labels are removed. Group labels in navigation are 12px, weight 600, sentence case.
- Small section labels such as "Needs you" are headings, not uppercase eyebrows.

## Shape and depth

| Token | Value | Use |
|---|---|---|
| `--radius-sm` | 6px | Small inline elements |
| `--radius-md` | 10px | Buttons, inputs, nav items |
| `--radius-lg` | 16px | Cards, tables, panels, notices |
| `--radius-xl` | 20px | Drawers, sheets, dialogs, the search palette |
| Pill | 999px | Chips, badges |

Radius scales with hierarchy: controls are smaller than the surfaces that contain them.

Depth is quiet. Cards use a 1px `neutral-200` border and, for primary surfaces only, the warm `--shadow-card` shadow. Drawers, sheets, dialogs and the search palette get a stronger shadow over a walnut scrim at roughly 32–40% opacity. No gradients.

## Core components

These are patterns, not a component library; build them as the pages need them.

- **Primary button**: `bg-blue-600 text-white`, 44px minimum height, `rounded-md`, 500 weight. At most one per view region.
- **Secondary button**: warm white fill, `neutral-300` border.
- **Soft action button**: `bg-blue-100 text-blue-800`, used for per-row fixes in lists such as the inbox.
- **Status badge**: a pill, 12px, 500 weight, always with a text label:
  - Draft: squash.
  - Reviewed: neutral (T14).
  - Committed: olive.
  - Reading: neutral (G1).
  - Couldn't read: tomato (G5).
- **Segmented control**: oat track with a raised warm-white selected segment. Used for small enumerations such as vendor kind, purchase status, and the list/map toggle.
- **Drawer**: a right-side panel, 460px wide on desktop, `rounded-xl`, with a sticky footer holding Cancel and the primary action. It keeps focus inside while open, closes on Escape and Cancel, and returns focus to the button that opened it.
  - **Unsaved input (D5):** when the form holds typed input, Escape, a backdrop click and Cancel don't close it. They show an inline squash bar in the footer, "Discard this {thing}? What you typed will be lost.", with Discard and Keep editing. Keep editing takes focus.
  - Validation errors show at the field and the input is kept.
  - While saving, the primary button is disabled.
- **Notice (D18)**: the one shared confirmation component. It is an inline region at the top of the main content, with `role="status"` and `aria-live="polite"`.
  - Tones: success (olive), info (neutral), error (tomato). It carries an optional action link and a dismiss button.
  - It can be carried across a navigation in router state. The destination page consumes that state on mount and replaces the history entry, so Back and reload do not show the notice again.
  - Only one notice shows at a time. It stays until dismissed or until the user navigates, except the shelf-price "Saved" notice, which clears after 3 seconds (G4).
  - Floating toasts are not used.
- **Empty state**: one sentence saying what to do next, plus the relevant action. A filtered-empty list names the filter and offers "Clear filters"; a truly empty list offers the first create action (G11). Never an empty table with no guidance.

## Accessibility floor

- Text contrast of at least 4.5:1 in both themes, including captions and text on tinted fills. Automated axe checks enforce it (11: UI-1.12).
- Visible focus on every interactive element: 2px herb outline with a 2px offset.
- Touch targets of at least 44×44px on phone and tablet layouts.
- Real `<button>`, `<a href>`, `<label>` and `<input>` elements; no click handlers on non-interactive elements.
- Respect `prefers-reduced-motion`. The design uses no decorative motion; transitions are limited to drawers, sheets and dialogs opening and closing.

## Map

The map uses the self-hosted PMTiles extract from the Phase 1 spec. Style the basemap to sit on the cream palette: desaturated land in the `neutral-50`/`neutral-100` range, water in a muted tide blue, roads in `neutral-200`/`neutral-300`, and labels in `neutral-600`. Provide a dark-mode style that follows the espresso neutrals.

Vendor pins are walnut (`neutral-800`), with herb for the selected pin. They keep their shape per vendor kind, so kinds stay distinguishable without colour (T13): square for a chain, circle for an independent, diamond for a market, triangle for a stand. The existing `kerp-pin--{kind}` classes and their tests stay. The map legend shows the four shapes.
