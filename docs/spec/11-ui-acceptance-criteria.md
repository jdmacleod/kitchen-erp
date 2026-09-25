# 11 — UI acceptance criteria

The UI work is organized into four sub-phases that can be built and merged independently, in order. A sub-phase is done when every criterion in its block passes, the test suite is green, and `docker compose up` yields a working system from a clean checkout.

UI-1 through UI-4 apply to the pages that exist today (Phases 1–2). Criteria marked **dormant** belong to a later phase and become due only when that phase is approved and built. Criteria marked **deferred** were moved out of this work on 2026-09-25 and are recorded in TODOS.md. Decision IDs in parentheses refer to that day's review record (see 08).

## UI-1 — Theme and polish

- **UI-1.1** `theme.css` is imported immediately after Tailwind and defines every shade the app uses. Class names change only in the semantic pre-pass that 08 lists (T12): information and selection move off blue, the badge tone map is introduced, links and the active nav become herb, and raw hex values are removed from the map and chart.
- **UI-1.2** Fraunces and Inter are self-hosted through the `@fontsource-variable` packages; the app makes no requests to Google Fonts.
- **UI-1.3** Page titles render in Fraunces, and body, tables and forms in Inter. Prices and quantities use tabular figures.
- **UI-1.4** Light mode uses the cream/walnut neutrals and dark mode the espresso neutrals, both following `prefers-color-scheme`. Dark captions use `neutral-400` (D19).
- **UI-1.5** Primary buttons, links, focus rings and the active nav item are herb green, and no default Tailwind blue remains visible anywhere, including the map, the chart and browser surfaces (G17).
- **UI-1.6** `CategoryChip` renders from `category_key` for every ingredient category in ingredient lists, product rows and purchase lines; a null key shows the neutral chip (D12).
- **UI-1.7** Backend unit and Hypothesis tests cover `categories.key()` (D12):
  - each synonym group;
  - case and whitespace handling;
  - null;
  - an unknown value;
  - idempotence.
- **UI-1.8** No all-caps labels remain; section and group labels are sentence case.
- **UI-1.9** The build line and status dot stay in the sidebar footer. "Log out everywhere" moves to Settings → System (T10).
- **UI-1.10** Status badges always include a text label: Draft is squash, Reviewed neutral, Committed olive, Couldn't read tomato (T14, G5).
- **UI-1.11** The map basemap uses the cream and espresso styles in 08. Pins are walnut with their kind shapes, and the selected pin is herb (T13).
- **UI-1.12** Automated contrast checks (axe in Playwright) pass at 4.5:1 for text in both themes on Home, Products, Ingredients, Vendors and Purchases.
- **UI-1.13** The chart series palette's six hues are recorded in 08, and each series also has its own line style (T13b).

## UI-2 — Navigation and global chrome

- **UI-2.1** The sidebar shows Home, the built sections, Catalog and Settings, in the order given in 09; only the active section expands.
- **UI-2.2** The sections shown are driven by the `features` list on the authenticated `/health` response (S4). The Phase 1–2 sections render before `/health` answers and while it fails. Features only add sections, and a 503 body that carries features still adds them (D11).
- **UI-2.3** Every route in 09's route table exists. Every old path redirects to its new route with its parameters, including `/map`, `/compare`, `/to-identify` and `/price-book/needs-bridge`.
- **UI-2.4 to UI-2.7** *Deferred* (S3): the kitchen switcher, the per-user current kitchen, kitchen tags, and kitchen-scoped endpoint rules. They return with Phase 4.
- **UI-2.8** ⌘K / Ctrl+K opens search from any page. Results are grouped by type with ingredients first, fully keyboard-navigable, and barcodes match exactly. `q` is 1–200 characters. Before typing, the palette shows device recents or the hint (G15).
- **UI-2.9** *Deferred* (S2): searchable actions.
- **UI-2.10** Capture offers the three modes in 09: a bottom sheet on phone and tablet, a dialog on desktop (G14).
  - With geolocation granted, it pre-fills the nearest adopted vendor location, labelled "Near".
  - Without geolocation, it pre-fills the last-used location, labelled "Last used · change" (G2).
- **UI-2.11** `GET /api/v1/inbox` returns receipt, couldn't-read, identify and bridge items, oldest first, with the fields listed in 09 plus `reading: {count, oldest_at}`.
  - A draft that belongs to a failed job is not listed twice (D4).
  - A failure in any kind returns an error, never a partial list (D6).
- **UI-2.12** Home renders the inbox as specified in 10.
  - Each row's action opens the page that resolves it, and resolving an item removes it without a manual refresh.
  - A response that arrives after a resolving mutation does not bring the item back.
- **UI-2.13** The Home nav item shows the count of inbox rows (G6). The count is hidden until the first response arrives (G16), and a failed request shows "!" (D6). The separate "Needs a bridge" and "To identify" nav items are gone.
- **UI-2.14** "New purchase" is not a nav item; it is reachable from Capture and as the primary action on Shop pages.
- **UI-2.15** Receipts being read show as "Reading n receipts…" on Home, not counted in the badge (G1). When the oldest one is older than `INGEST_STALL_MINUTES` (default 10), the line reads "Reading is taking longer than usual · Check System" (D21).
- **UI-2.16** The Notice carries confirmations across a navigation (D18).
  - Back and reload do not show it again, and only one shows at a time.
  - An action in it can be reached by keyboard.
  - The shelf-price "Saved" notice clears after 3 seconds.

## UI-3 — Page layouts

- **UI-3.1** Every page uses the header pattern in 10: a Fraunces title, a one-line description, and one primary action top-right.
- **UI-3.2** No create form sits above a list. Products, Ingredients and Vendors create in a right-side drawer, with Cancel and the primary action in a sticky footer.
- **UI-3.3** Drawers keep focus inside, close on Escape and on Cancel, and return focus to the button that opened them. With typed input, Escape, a backdrop click and Cancel show the Keep editing / Discard bar instead of closing, and focus goes to Keep editing (D5).
- **UI-3.4** Products shows search, category filter chips, "Show inactive", and the five-column table in 10. Search and the category filter run on the server and find matches beyond the first page, and rows are ordered by name (D12, T16).
- **UI-3.5** The add product drawer expands the density section with an explanation when the pack unit can't convert to the ingredient's canonical unit. Saving without a density is allowed. A Bridge inbox item appears when the first price for that product is observed (T7).
- **UI-3.6** Ingredients follows the Products pattern, and the category field suggests the nine known categories.
- **UI-3.7** Vendors shows search, the kind segmented control, the list/map toggle in the URL, and the card grid in 10. The map view keeps pin-drop creation (T9).
- **UI-3.8** "Find nearby" opens the OpenStreetMap adoption flow in a dialog. It is pre-set to the only home base when there is one; with no home base, it directs the user to add a kitchen first.
- **UI-3.9** The ingredient hub shows the header, summary strip, prices by vendor and right column in 10.
  - Cards for unbuilt phases (stock, recipes, add to list) are omitted, not shown empty.
  - The 90-day sparkline comes from `GET /api/v1/ingredients/{id}/price-history`, and hides with fewer than two points (D22).
- **UI-3.10** Prices by vendor are sorted by normalized unit price, and describe each vendor's pricing scope as "Same price at every location" or "Price set per location" (T15). Prices that can't be compared sort last with the pack price and a link to add a density, and are excluded from Best recent price (G8).
- **UI-3.11** Every list page has an empty state with one sentence and the relevant action. Filtered-empty and truly-empty states differ (G11).
- **UI-3.12** Receipt review keeps Commit disabled, with its reason, until a location is set. After commit, it shows the committed notice and offers "Next draft" when drafts remain (G9).
- **UI-3.13** After a drawer save, the new row takes focus if it is visible; otherwise the Notice links to it and that link takes focus (G10).

## UI-4 — Phone and tablet

- **UI-4.1** Below 1024px the sidebar is replaced by the five-tab bar in 09, with Capture raised in the centre and the second tab labelled "Purchases" (G19, T18).
- **UI-4.2** All interactive elements on phone and tablet layouts are at least 44×44px. Layouts work from 360px to 1023px wide with no horizontal scroll, which e2e checks at 360, 375, 390 and 1000px, comparing against the configured viewport width.
- **UI-4.3** The phone Home shows the wordmark, the top three inbox items with "See all" expanding in place, and recent purchases (G13, G6).
- **UI-4.4** Capture opens as a bottom sheet with the three modes and the detected or last-used store, labelled as in UI-2.10.
- **UI-4.5** Log a shelf price accepts a barcode or a product name, lists the last five products logged at the store before typing, and shows the matched product, the price field, the on-sale toggle, last paid here and best known (G3). Camera scanning is *deferred* to Phase 6 (S1).
- **UI-4.6** The shelf price field opens a decimal keypad. Save and "Save and scan another" are anchored in the bottom thumb zone and behave as 10 specifies (G4).
- **UI-4.7** An unknown barcode offers to create the product with the barcode pre-filled, then returns to price entry.
- **UI-4.8** *Dormant* until Phase 4 (T4): the at-the-store screen. Editing an item's price there records a shelf price observation, and "Done here" creates a purchase linked to the trip plan.
- **UI-4.9** The More tab lists Shop's other pages, Catalog and Settings, plus Cook and Stock once they are built.
- **UI-4.10** Receipt review on phone shows one line per card, "Needs you" lines first, with 44px suggestion buttons and Commit anchored in the thumb zone (G18).
