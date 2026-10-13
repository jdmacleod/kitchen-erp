# Kitchen ERP design system, as the code implements it

This file describes the frontend as it ships, so that UI work and design reviews calibrate against what
Kitchen ERP actually does rather than generic principles or an ideal. Every token, class and pattern below is
cited to a file. The normative design system is `docs/spec/08-ui-design-system.md` ("Market"): where this file
and 08 disagree on intent, 08 wins; where they disagree on fact, the code wins and this file is wrong. Update
this file in the same PR as any visual change, as `docs/spec/08` is updated when the intent changes.

The conventions this file leans on live in `CLAUDE.md` (UI conventions),
`docs/spec/09-information-architecture.md` (sections, routes, the phone shell), `docs/spec/10-page-layouts.md`
(each page) and `docs/spec/11-ui-acceptance-criteria.md` (the UI-n.m criteria the e2e suite checks). 08–11 are
not repeated here except where the rule is needed to make sense of the code.

> Tooling note: gstack's `/design-review` and `/ship` read a root `DESIGN.md`.
> Keeping this file true makes those checks Kitchen ERP-aware.

## 1. Direction

Cream and walnut, one herb-green action colour, and produce colours that mean only "ingredient category".
Fraunces for page titles, Inter for everything else, softer corners than an admin tool. The category chips are
the one expressive element; everything around them stays quiet. The reasoning is in `docs/spec/08`, Direction.

## 2. Colour

All colour comes from five Tailwind scales remapped to OKLCH in `frontend/src/theme.css` (`@theme` block).
Components use the stock utility names (`bg-blue-600`, `text-neutral-600`); the remap is the single source of
truth and there is no second colour system.

| Scale | Role | Meaning the code attaches to it |
| --- | --- | --- |
| `neutral-*` | Pantry neutrals, cream to espresso | Surfaces, text, borders, information and selection. |
| `blue-*` | Herb green (hue 150) | "You can act on this" and nothing else: buttons, links, focus, active nav, the Capture button. Never an info tint (`theme.test.ts` fails on `bg-blue-50/200/300/950`). |
| `red-*` | Tomato (hue 32) | Errors, destructive confirms, "Couldn't read". |
| `amber-*` | Squash (hue 85 to 56) | Drafts, stale prices, unconfirmed, guesses, the inbox count, the discard bar. |
| `green-*` | Olive (hue 118) | Success, committed, cheapest, on sale. Off the herb hue so a success badge never reads as a button. |
| `white` | Warm white | Card and input surfaces (`--color-white` is remapped too). |

`badgeTones` in `frontend/src/components/catalog/fields.tsx` pins meaning to scale (`neutral`, `good`, `warn`,
`danger`; never herb), and every badge carries a text label beside its colour.

### Tokens that matter

Light values are the stock `dark:`-less class; dark values are the `dark:` pair the code actually uses. The
scale values themselves are in `theme.css`.

| Role | Light | Dark | Where |
| --- | --- | --- | --- |
| Page ground | `bg-neutral-50` | `bg-neutral-950` | `frontend/src/index.css` body |
| Body text | `text-neutral-900` | `text-neutral-100` | `index.css` body |
| Secondary text, captions | `text-neutral-600` | `text-neutral-400` | `ui.tsx` Field hint, PageHeader description; 08 rule 4 (D19) |
| Card surface and border | `bg-white border-neutral-200` | `bg-neutral-900 border-neutral-800` | `ui.tsx` Card |
| Sidebar | `bg-neutral-100` | `bg-neutral-900/60` | `AppShell.tsx` aside |
| Input border | `border-neutral-300` | `border-neutral-700` | `ui.tsx` Field, `fields.tsx` inputClass |
| Primary action fill | `bg-blue-600 text-white` | same (blue-500 on white is 3.7:1) | `ui.tsx` buttonVariants.primary |
| Link | `blue-700` | `blue-300` | `theme.css` base `a` rule |
| Focus outline | `outline-blue-600` | `outline-blue-400` | `ui.tsx` focusRing |
| Active nav section | `bg-blue-100 text-blue-800` | `bg-blue-900 text-blue-100` | `Nav.tsx` sectionClass |
| Error surface | `bg-red-50 text-red-800 border-red-300` | `bg-red-950 text-red-200 border-red-900` | `ui.tsx` alertTones.error |
| Success surface | `bg-green-50 text-green-800` | `bg-green-950 text-green-200` | `alertTones.success` |
| Warn surface | `bg-amber-50 text-amber-900` | `bg-amber-950 text-amber-200` | `alertTones.warn` |
| Info surface | `bg-neutral-100 text-neutral-800` | `bg-neutral-900 text-neutral-200` | `alertTones.info` |
| Scrim | `bg-neutral-950/40` | same | `Drawer.tsx`, `Dialog.tsx` |
| Text selection | `blue-100` | `blue-900` | `theme.css` `::selection` |
| Caret, accent, scrollbar | `blue-600`, `neutral-300` | `neutral-700` | `theme.css` `:root` |
| Radius | `--radius-sm` 6px, `-md` 10px, `-lg` 16px, `-xl` 20px | | `theme.css` |
| Card shadow | `--shadow-card` (two walnut layers at 5%) | | `theme.css`; see §12, unused |
| Cutout shadow | `--shadow-cutout` 12% | 45% | `theme.css`, class `cutout-shadow` |
| Fonts | `--font-sans` Inter Variable, `--font-display` Fraunces Variable | | `theme.css`; loaded in `main.tsx` from `@fontsource-variable` |

### The one hex exception

`frontend/src/lib/palette.ts` holds sRGB mirrors of OKLCH sources for the two surfaces that cannot read CSS:
the MapLibre basemap (`basemapColours`, `basemapLabelColours`, used by `components/geo/MapView.tsx`) and the
SVG price charts (`SERIES_COLOURS`, used by `components/pricebook/PriceHistoryChart.tsx` and
`components/cook/SnapshotChart.tsx`). `frontend/src/test/theme.test.ts` recomputes every mirror from its OKLCH
source, checks the neutrals against `theme.css`, fails on any other raw hex in `frontend/src`, and fails on
any Tailwind colour scale outside the five. Each chart series also has its own dash pattern (`DASHES` in
`PriceHistoryChart.tsx`), so colour is never the only signal.

### Dark mode

There is no toggle. `index.css` sets `color-scheme: light dark`, Tailwind v4's `dark:` variant follows
`prefers-color-scheme`, and `theme.css` repeats the same media query for the base `a`, `::selection`,
scrollbar and `.cat` tints. The e2e contrast spec emulates both schemes.

## 3. Category accents

Produce colours appear only through the `.cat` classes in `theme.css`. Each category is a hue and chroma
(`.cat-produce { --cat-h: 128; --cat-c: 0.130 }` and so on, nine keys); `--cat-bg`, `--cat-fg` and `--cat-dot`
are derived from them for light and dark. `.cat-chip` is the tinted pill with a solid dot; `.cat-placeholder`
is the no-photo square (PD15); `.cat-edge` (a coloured leading edge for rows) is defined and has 0 uses in
`.tsx`.

`frontend/src/components/CategoryChip.tsx` exports `CategoryChip` (`category`, `categoryKey`, renders nothing
without a label), `categoryClass(key)` (`"cat cat-dairy"` or `"cat"`), `CATEGORY_KEYS`, and `ProductCategory`
(the group chip plus the ingredient's own wording). It has no synonym map: normalisation is owned by
`backend/app/catalog/categories.py` (`SYNONYMS`, `KEYS`), and every ingredient response carries `category_key`
(D12).

```tsx
// pages/catalog/IngredientsPage.tsx
<CategoryChip category={ingredient.category} categoryKey={ingredient.category_key} />
```
Adoption: `<CategoryChip` 11 uses in 10 files, `<ProductCategory` 1.

## 4. Typography and figures

- `h1` is Fraunces by a base rule in `theme.css` (weight 600, `opsz 72`, `SOFT 50`, tracking −0.015em).
  `PageHeader` sizes it `text-3xl md:text-[2.5rem]`: 30px on phone, 40px from `md`.
- Everything else is Inter via `html { font-family: var(--font-sans) }` with `cv11` and `ss01`. Section and
  card headings opt into Fraunces with `font-display` (19 uses); most do not (§12).
- `font-mono` appears 21 times, for paths, env names and OpenStreetMap syntax.

### Observed scale

Counts of size utilities across `frontend/src/**/*.tsx`:

| Utility | Count | Typical role |
| --- | --- | --- |
| `text-sm` | 385 | Body, controls, table cells |
| `text-xs` | 202 | Captions, badges, hints, tab labels |
| `text-lg` | 54 | Section `h2` (`text-lg font-medium`, 43 of them) |
| `text-base` | 26 | Inputs (so iOS does not zoom), empty-state titles |
| `text-xl` | 8 | Drawer, sheet and dialog titles (`font-display text-xl`) |
| `text-2xl` to `text-4xl` | 6 | The Login and Clip `h1`, an ingredient's best price, the shelf-price entry |

### Figures

- Tabular figures are set once in `theme.css` for `table` and `input[type="number"]`, and added by class in 21
  files (45 uses of `tabular-nums`) where a figure sits outside a table.
- Money is a decimal string end to end. `frontend/src/lib/decimal.ts` does exact BigInt arithmetic (`add`,
  `mul`, `div`, `roundTo`, half-even) and `formatMoney(a, min = 2, max = 2)` prints `$4.49`. Purchase-entry
  arithmetic carries `MONEY_PLACES` (4) until it is sent; the screen never shows more than the formatter's
  places.
- Unit prices are decided by the server. `backend/app/units/display.py` restates a stored per-g, per-ml or
  per-each price per lb, oz, fl oz or each (kg and L with `UNIT_DISPLAY=metric`), per oz only when the
  ingredient's usual pack is under a pound, and rounds to 2 places, or 3 under $1 (`round_price`).
  `backend/app/services/unit_display.py` attaches the result as `display_unit_price` and `display_unit` to
  every price response. `formatUnitPrice` in `components/pricebook/PriceAge.tsx` prints the digits as sent:
  `$4.49/lb`, `$0.269/fl oz`, `$1.29 each`.
- A missing value is an em dash. `formatUnitPrice` returns `—` for a missing price; so do `QualityStars`,
  `CostTable.tsx` and `UnitSelect`'s empty option.
- Precision follows the surface. A purchase line, a receipt total and a compare cell all show cents
  (`formatMoney`); a unit price shows the server's 2 or 3 places; `moneyForQty` prints `$23.12 / 2 each` so a
  two-pack is never mistaken for a price per one. There is no whole-dollar surface.

## 5. Shape, depth and spacing

- Radius tokens: `rounded-md` (10px) for buttons, inputs and nav items, 130 uses; `rounded-lg` (16px) for
  cards and the segmented track, 31; `rounded-xl` (20px) for drawers and dialogs, 4; `rounded-full` for pills
  and avatars, 15. Off-token: bare `rounded` (stock 4px) 81 uses and `rounded-2xl` (stock 16px) 6, see §12.
- Shadows: stock `shadow` 16 (photo tiles), `shadow-sm` 4 (raised selected segment, active nav item),
  `shadow-xl` 2 (Drawer, Dialog), `shadow-lg` 2 (the raised Capture button). The theme's `--shadow-card` has 0
  class uses; `cutout-shadow` 3 (`ProductThumb.tsx`, `ProductPhotosCard.tsx`).
- Spacing primitives in use: `gap-2` 237, `gap-3` 115, `gap-1` 88, `gap-4` 45, `gap-6` 14; `py-2` 156, `px-2`
  72, `px-3` 62, `px-4` 27, `py-3` 18; `p-3` 26, `p-4` 13 (Card), `p-5` 4 (dialog bodies). Nothing finer than
  0.5 is used.
- Touch targets: every control is `min-h-11 lg:min-h-10` or `min-h-11 lg:min-h-9` (44px below `lg`, 40 or 36px
  from `lg`). `tapTarget` in `ui.tsx` (`inline-flex min-h-11 items-center lg:inline lg:min-h-0`) grows an
  inline text link to 44px on phone and leaves it inline on desktop.

## 6. Layout shell

`frontend/src/components/AppShell.tsx` is the authenticated layout. The one breakpoint that changes the shell
is `lg` (1024px, `LG_QUERY` in `lib/useMediaQuery.ts`); pages keep their own breakpoints (09, Phone and
tablet).

- At `lg` and wider: a sticky 240px sidebar (`w-60`, `bg-neutral-100`) with the wordmark, a Search button
  showing `⌘K` or `Ctrl K`, the Capture button and `Nav`. `main` is `max-w-4xl` (`max-w-7xl` when a page calls
  `setWide`), `px-4 md:px-8 lg:py-8`.
- Below `lg`: a sticky header with the wordmark and the person's initials, and `TabBar` fixed at the bottom
  (Home, Purchases, a raised 56px herb Capture circle, Search, More). `main` pads its bottom by 6.5rem plus
  the safe area so the page ends above the bar. Task screens (`TASK_PATHS`, product review, a trip's stop at `/plan/trips/:id/stop/:seq`) hide both.
- `Nav.tsx` holds `MAIN_SECTIONS` (Home, Cook, Shop) and `FOOTER_SECTIONS` (Catalog, Settings) in 09's order.
  `visibleSections()` keeps a section when its `feature` is in `/health`'s `features` list or in
  `DEFAULT_FEATURES` (`catalog`, `shop`), so Phase 1–2 sections render before `/health` answers (D11) and a
  feature can only add. Only the active section expands.
- `MoreSheet` in `TabBar.tsx` lists every page that has no tab, grouped by section, as a bottom sheet (`Dialog
  placement="sheet"`), not a route.
- One overlay at a time (`search`, `capture`, `more`, `shelf`); any navigation closes it except the
  shelf-price drawer, which can hold typed input.
- A skip link to `#main`; `main` has `tabIndex={-1}` so a closed dialog can send focus to the new page.

Every page starts with `PageHeader` (`ui.tsx`): the `h1`, a one-line description in `neutral-600`, and the
actions with the primary one last (10, Desktop page pattern). Below `lg`, secondary actions collapse into
`MoreActionsDialog`. Create forms open in a `Drawer`; the search palette's "Add …" actions link to the list
page with `?new=1`, which `ProductsPage.tsx`, `IngredientsPage.tsx` and `VendorsPage.tsx` read to open it
(UI-2.9). Confirmations are the `Notice` at the top of `main`, which can ride along a navigation in router
state (§7).

## 7. Component vocabulary

The primitives live in `frontend/src/components/`. `ui.tsx` holds the small ones; the rest have a file each.
Counts exclude `src/test`.

### `Button` (`ui.tsx`)

`<button type="button">` by default, `inline-flex min-h-11 lg:min-h-10 rounded-md px-3 py-2 text-sm
font-medium transition-colors`, plus `focusRing`. Variants: `primary` (herb fill, at most one per view
region), `secondary` (warm white, `neutral-300` border), `danger` (outlined tomato), `dangerFill` (the one
confirming button of a destructive action), `ghost`. `primaryLinkClass` and `secondaryLinkClass` give a `Link`
the same look.

```tsx
// pages/geo/VendorsPage.tsx
<Button variant="secondary" onClick={a.open}>{a.label}</Button>
```
Adoption: `<Button` 275 in 70 files (`secondary` 159, `ghost` 38, `danger` 21, `dangerFill` 3); raw `<button`
29, all in the shell, tab bar, Notice and list rows where the primitive's padding does not fit.

### `Field`, `SelectField`, `TextAreaField`, `RadioGroup`, `Badge`

`Field` (`ui.tsx`) is a labelled `<input>`: `id` and `label` are required, `hint` renders a `<p>` wired by
`aria-describedby`, the input is `text-base` (no iOS zoom) with `inputClass` styling. `fields.tsx` exports the
same `inputClass`, `labelClass` and `hintClass` for `SelectField` (a native `<select>`), `TextAreaField`,
`RadioGroup` (a `<fieldset>`), the `Badge` tone map and `Disclosure` (`<details>`).

```tsx
// pages/catalog/IngredientsPage.tsx
<Field id="new-ingredient-name" label="Name" autoComplete="off" required
  value={form.name} onChange={(e) => set("name", e.target.value)} />
```
Adoption: `<Field` 82 in 28 files; raw `<input` 53 (checkboxes, the combobox, line-entry cells, all on
`inputClass`). Native `<select>` 15 in 12 files, every one through `SelectField` or `inputClass`; there is no
custom listbox and 08 does not ask for one.

### `Alert` (`ui.tsx`)

`rounded-md border px-3 py-2 text-sm` in `alertTones` (`error`, `success`, `info`, `warn`); `role="alert"` for
`error`, `role="status"` otherwise. For a state the page is in; the `Notice` is for something that just
happened.

```tsx
// pages/LoginPage.tsx
{login.isError ? <Alert tone="error">{errorMessage(login.error)}</Alert> : null}
```
Adoption: 181 in 55 files. An error is never shown as an empty state.

### `PageHeader`, `EmptyState`, `Card`, `Centered` (`ui.tsx`)

`PageHeader` (44 in 30 files) is described in §6. `EmptyState` (33 in 25) is a dashed `neutral-300` `<section
aria-label={title}>` with one sentence and the way out: a filtered-empty list names the filter and offers
"Clear filters", a truly empty one offers the first create action (G11).

```tsx
// pages/cook/RecipesPage.tsx
<EmptyState title={filteredEmptyTitle(q, status, completeness)}
  action={<Button variant="secondary" onClick={clearFilters}>Clear filters</Button>} />
```

`Card` (53 in 34) is a `<section>` with `rounded-lg border border-neutral-200 bg-white p-4` and no shadow;
`Centered` (3) frames the login and error pages.

### `Drawer` (`Drawer.tsx`)

The right-side create form (08, Drawer; D5). Mounted only while open; `role="dialog" aria-modal
aria-labelledby` on a panel that is full-width on a phone and `sm:w-[460px] sm:rounded-xl` above; the footer
holds Cancel and a `type="submit" form={formId}` primary button, so the form itself lives in `children`.
`useModal` gives it the focus trap, Escape and focus return. With `dirty`, Escape, the scrim and Cancel show
the squash "Discard this {thing}?" bar and focus "Keep editing".

```tsx
// pages/catalog/ProductsPage.tsx
<Drawer title="Add product" thing="product" dirty={dirty} onClose={onClose}
  formId="add-product" primaryLabel="Add product" busy={create.isPending} busyLabel="Adding…">
  <ProductForm layout="drawer" formId="add-product" … />
</Drawer>
```
Adoption: 8 in 7 files, including the shelf-price, vendor import and suggestion drawers.

### `Dialog` and `useModal` (`Dialog.tsx`)

`useModal(panel, open, onEscape, initialFocus?, returnFocus?)` is the keyboard contract every modal shares:
focus moves in, Tab wraps inside `FOCUSABLE`, Escape calls back, and on close focus returns to the opener, or
to `#main` when `returnFocus.current` is false because the close navigated. `Dialog` renders the scrim and
panel with `placement` `center` (default, `max-w-[40rem] rounded-xl`), `sheet` (rises from the bottom below
`lg`, `rounded-t-2xl`, a small centred dialog from `lg`) or `screen` (full screen below `lg`, for Search).

```tsx
// components/MoreActionsDialog.tsx
<Dialog open onClose={onClose} labelledBy={titleId} placement="sheet" className="p-5">
  <h2 id={titleId} className="mb-3 font-display text-xl">More actions</h2>
  …
</Dialog>
```
Adoption: 7 sites (`MoreActionsDialog`, `MoreSheet`, `CaptureSheet`, `SearchPalette`, `LinkOsmDialog`, two in
`VendorsPage.tsx`). One hand-rolled overlay remains: `ReviewPurchase.tsx` line 517 (`fixed inset-0 …
bg-black/40`), see §12. `MoreActionsDialog.tsx` is the sheet that holds a page's secondary actions below `lg`
(D15): `secondary` or `danger` `Button`s and a `ghost` Close; 3 sites.

### `SegmentedControl` (`SegmentedControl.tsx`)

A small enumeration as one control: `role="group" aria-label={label}` around `<button aria-pressed>`s on an
oat `bg-neutral-200 rounded-lg p-0.5` track; the selected segment is `bg-white shadow-sm` (dark:
`neutral-950`). Plain Tab stops, no roving tabindex. Generic over the value union.

```tsx
// pages/geo/VendorsPage.tsx
<SegmentedControl label="View" value={view} onChange={(v) => setParam("view", v === "map" ? "map" : null)}
  options={[{ value: "list", label: "List" }, { value: "map", label: "Map" }]} />
```
Adoption: 9 in 7 files (purchase status, vendor kind, list/map, and so on).

### `Notice` (`Notice.tsx`)

The one shared confirmation (D18). `NoticeProvider` sits inside `main` in `AppShell`; `NoticeRegion` is an
always-present `aria-live="polite" aria-atomic` div, and the notice inside it is `role="status"` in
`alertTones[tone]` (`success`, `info`, `error`) with `rounded-2xl`, an optional action `Link` and a 44px
Dismiss button. `useNotice()` returns `show`, `update`, `dismiss`; `useNavigateWithNotice()` navigates with
the notice in router state, and the provider picks it up on the destination, replaces the history entry and
drops it from state, so Back and reload do not replay it. One at a time; it belongs to the pathname it was
shown on; `timeoutMs` auto-clears (the shelf-price "Saved" uses 3 s, G4).

```tsx
// pages/catalog/ProductsPage.tsx
notice.show({ tone: "success", message: `Added ${productTitle(product)}.`,
  action: { label: "Open it", to: `/catalog/products/${product.id}` }, focusAction: true });
```
Adoption: `useNotice()` 32 call sites, `useNavigateWithNotice()` 7. Floating toasts: `grep -rni toast
frontend/src` finds only the comment in `Notice.tsx` saying there are none.

### `SearchPalette` (`SearchPalette.tsx`)

A `Dialog placement="screen"` whose input is a `role="combobox"` (`aria-expanded`, `aria-controls`,
`aria-autocomplete="list"`, `aria-activedescendant`) over a listbox of grouped options: ingredients, products,
vendors, recipes, then the actions and pages of the sections that are built. ⌘K / Ctrl K opens it anywhere;
choosing a result sets `returnFocus` false so focus lands on the new page; below `lg` a Cancel button stands
in for Escape.

### `Nav` and `TabBar`

Described in §6. `<nav aria-label="Main">` sets `aria-current` on the section link; `<nav aria-label="Tabs">`
sets `aria-haspopup="dialog" aria-expanded` on More. `InboxBadge` is a squash pill with an `aria-label`
sentence, or a neutral "!" when the inbox could not be checked (D6).

## 8. Motion

Almost none. `grep -rhoE "transition(-[a-z]+)?|animate-[a-z-]+|duration-[0-9]+" frontend/src` finds
`transition-colors` once (`Button`), `transition-transform` once (a disclosure chevron in
`ShelfPricePage.tsx`) and `animate-pulse` twice (loading skeletons in `landing/InboxList.tsx` and
`ProductReviewPage.tsx`). Drawers, sheets and dialogs mount and unmount with no transition.
`prefers-reduced-motion` is not referenced anywhere in `frontend/src` or `e2e`; the two skeleton pulses are
the only thing it would affect (§12).

## 9. Value formatting

- Money and quantities: `frontend/src/lib/decimal.ts` (§4). Values are decimal strings from the API; nothing
  is turned into a JavaScript number except to place a mark on a chart (`SnapshotChart.tsx`).
- Unit prices: the server's `display_unit_price` and `display_unit`, printed by `formatUnitPrice` (§4). The
  browser never converts a price.
- Dates: `frontend/src/lib/format.ts`. Timestamps are UTC ISO-8601 from the API and localised with
  `Intl.DateTimeFormat(undefined, …)`: `formatDateTime` (medium date, short time), `formatDate`, `formatTime`,
  and `formatRelative` ("3 hours ago", then the date past a month). `formatCalendarDate` renders a plain
  `YYYY-MM-DD` as written, never shifted by zone, because a purchase date is a day in the household's
  calendar, not a moment.
- Empty: `""` from the date helpers, `—` from the figure helpers.

## 10. Accessibility floor

The floor is in 08 (Accessibility floor) and 11 (UI-1.12, UI-4.2, UI-5.8, UI-6.6, UI-7.18): 4.5:1 text
contrast in both themes, a visible 2px herb outline with 2px offset on every interactive element, 44×44px
targets below 1024px, real `<button>`, `<a href>`, `<label>` and `<input>`.

What the code does: `focusRing` in `ui.tsx` is the outline, applied by class (there is no global
`:focus-visible` rule in `index.css` or `theme.css`). `inputClass` in `fields.tsx` carries it to every field.
Captions use `neutral-600` / `neutral-400`; `text-neutral-500` appears twice, as a placeholder colour in
`SearchPalette.tsx` and on a disabled row in `LinkOsmDialog.tsx`.

Reality check:

- `e2e/tests/contrast.spec.ts` runs axe `color-contrast` in light and dark on Home, Products, Ingredients,
  Vendors, Purchases, Recipes, Resolve recipe names and one recipe page, desktop project only, on the shared
  stack's data as it stands. UI-1.12 names the first five; the Cook three were added with UI-7.18. Other pages
  are covered only by the class-level rules in `theme.test.ts`.
- `e2e/tests/narrow-phone.spec.ts` measures every rendered control in `header`, `nav[aria-label=Tabs]` and
  `main` on `/`, `/shop/purchases`, `/shop/receipts`, `/catalog/products`, `/catalog/ingredients`,
  `/catalog/vendors` and `/settings/kitchens`, plus the open More, Search and Capture sheets, and fails on
  anything under 43.5px; it also checks for sideways scroll at 390, 375, 360 and 1000px
  (`e2e/playwright.config.ts` projects). Detail pages, drawers and Cook are not measured.
- `grep -rL focusRing frontend/src/components --include='*.tsx'` lists six files with interactive elements.
  Four use `inputClass`, which carries the ring (`Combobox.tsx`, `CorrectLines.tsx`, `PurchaseForm.tsx`,
  `OpeningHoursInput.tsx`), and `LocationGuard.tsx` uses `primaryLinkClass`. The one gap is the `<input
  type="file">` in `geo/VendorImportDrawer.tsx` (`min-h-11 text-sm`), which falls back to the browser's
  default outline.

## 11. Screenshots index

`docs/screenshots/` is recaptured from the demo stack by `e2e/capture/screenshots.spec.ts` (`make
screenshots`), never from real data. The README embeds the first four.

| File | Shows |
| --- | --- |
| `compare.png` | Compare prices: four ingredients across vendors, the cheapest qualifying price marked in each row. |
| `product-prices.png` | A product page: price history, one line per vendor location, with the observations below. |
| `purchases.png` | The purchases list, each row with its status pill. |
| `purchase-detail.png` | One purchase: its lines and the observations they produced. |
| `recipes.png` | Cook: the recipes list with costing figures and filters. |
| `recipes-phone.png` | The same list in the phone shell at 390px. |
| `recipe-cost.png` | A recipe page: the rendered recipe beside its cost-by-line table. |
| `recipe-cost-phone.png` | The recipe page at 390px, the cost table as stacked blocks. |
| `recipes-resolve.png` | Resolve recipe names: the queue of unmatched ingredient names. |
| `recipes-resolve-phone.png` | The resolve page at 390px, rows stacked with the count strip pinned. |

## 12. Known drift and convergence

Each grep was run on `frontend/src` at the commit this file was written. Re-run them before changing a line
here.

1. Raw hex outside `palette.ts`: RESOLVED. `grep -rnE "#[0-9a-fA-F]{3,8}\b" frontend/src --include='*.ts'
   --include='*.tsx' --include='*.css' | grep -v lib/palette.ts` finds one line, an assertion in
   `test/cook-recipe-page.test.tsx` that the chart stroke is series 1. `theme.test.ts` enforces it.
2. Tailwind colour scales outside the five (`slate`, `gray`, `zinc`, `stone`, `indigo`, `emerald`, `rose`,
   `orange`, `purple`, `sky`, `teal`, `cyan`, `violet`, `fuchsia`, `pink`, `lime`, `yellow`): RESOLVED, 0
   hits; `theme.test.ts` enforces it. Open: stock `black` and `white` alpha tints, which the remap does not
   cover: `hover:bg-black/5` 2 and `dark:hover:bg-white/10` 2 (the Dismiss buttons in `Notice.tsx` and
   `NewVersionNotice.tsx`), `bg-black/40` 1 (the hand-rolled overlay in `purchases/ReviewPurchase.tsx` line
   517, which should be a `Dialog`).
3. Toasts: RESOLVED. `grep -rni toast frontend/src` finds only the comment in `Notice.tsx`. `alert(` appears
   once, in `lib/bookmarklet.ts`, which runs inside the retailer's page where the app's chrome does not exist.
4. All-caps labels: RESOLVED. `grep -rn uppercase frontend/src` finds one `first-letter:uppercase` in
   `settings/SystemPage.tsx`, a capital letter, not a caps label.
5. Native `<select>`: by design, not drift. 15 in 12 files (plus one test), all styled by `inputClass` through
   `SelectField` or directly. 08 asks for no custom listbox; revisit only if the OS popover becomes a problem.
6. Radius off the token scale: open. Bare `rounded` (stock 4px, below `--radius-sm`) has 81 uses, mostly
   `kbd`, `code`, inline links and skeleton bars; `rounded-2xl` (stock 16px, numerically `--radius-lg`) has 6,
   including the `Notice`; `rounded-t-2xl` and `rounded-none` one each. Converge `rounded-2xl` to `rounded-lg`
   and bare `rounded` to `rounded-sm` when touching a file; add no new ones.
7. `--shadow-card`: open. Defined in `theme.css` and given 0 uses; the 16 bare `shadow` uses are the photo
   tiles. Use it on primary surfaces as 08 asks, or drop the token.
8. Section headings: open. 08 says "Section heading: Fraunces 19–21px, weight 600, via `font-display`". The
   code's `h2` classes are `text-lg font-medium` (43, Inter 18px/500) against 6 `font-display` ones (drawer
   and sheet titles). Decide which stands and change the other.
9. Primary button height: open on paper only. 08's component line says "44px minimum height" with no
   breakpoint; `Button` is `min-h-11 lg:min-h-10` (40px from 1024px), following 08's accessibility floor,
   which scopes 44px to phone and tablet. Amend the component line.
10. Motion: open. 08 says "transitions are limited to drawers, sheets and dialogs opening and closing" and
    "Respect `prefers-reduced-motion`". The code has no overlay transitions and no reduced-motion query (§8).
    Either add a `motion-reduce:animate-none` to the two skeleton pulses, or amend 08 to say there is nothing
    to reduce.
11. Fonts: 08 says "Import both in `main.tsx`, latin subset only"; `main.tsx` imports
    `@fontsource-variable/fraunces/full.css` (every axis, needed for `opsz` and `SOFT`) and
    `@fontsource-variable/inter/index.css`. Amend 08.
12. `docs/design/`: a historical handoff, superseded by 08–11. Its `theme.css` and `CategoryChip.tsx` came
    with the Market handoff (#38, 2026-09-25); that `CategoryChip.tsx` has its own `SYNONYMS` map, which D12
    forbids. `kitchen-erp-ui-spec.zip` sits beside them locally but is gitignored (`*.zip`). Nothing imports
    from the directory; delete it in a later PR and do not read it as a reference.

_Maintenance: this file is hand-derived from the code. When you change a token in `frontend/src/theme.css`,
a primitive in `frontend/src/components/`, `lib/palette.ts`, `lib/format.ts`, `lib/decimal.ts` or
`units/display.py`, update the matching section in the same PR. Before editing §12, re-run its greps and the
ones in §4, §5 and §8; `pnpm test` in `frontend/` runs `theme.test.ts`, which holds the hex and scale rules,
and `make e2e` runs the contrast and narrow-phone specs. Adopted conventions change in `docs/spec/08`, not
here._
