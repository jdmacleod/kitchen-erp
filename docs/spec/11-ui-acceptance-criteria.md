# 11 — UI acceptance criteria

The UI work is organized into four sub-phases that can be built and merged independently, in order. A sub-phase is done when every criterion in its block passes, the test suite is green, and `docker compose up` yields a working system from a clean checkout.

UI-1 through UI-4 apply to the pages that exist today (Phases 1–2). UI-5 was added with sub-phase 1G on 2026-09-30 and builds with it. Criteria marked **dormant** belong to a later phase and become due only when that phase is approved and built. Criteria marked **deferred** were moved out of this work on 2026-09-25 and are recorded in TODOS.md. Decision IDs in parentheses refer to that day's review record (see 08).

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
- **UI-1.10** Status badges always include a text label: Draft is squash, Careful look squash, Reviewed neutral, Committed olive, Couldn't read tomato (T14, G5, #121).
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
- **UI-2.10** Capture offers the modes in 09 (four since 2L, PD12): a bottom sheet on phone and tablet, a dialog on desktop (G14).
  - With geolocation granted, it pre-fills the nearest adopted vendor location, labelled "Near".
  - Without geolocation, it pre-fills the last-used location, labelled "Last used · change" (G2).
- **UI-2.11** `GET /api/v1/inbox` returns receipt, careful-look, couldn't-read, identify and bridge items, oldest first, with the fields listed in 09 plus `reading: {count, oldest_at}`. A held draft is one careful-look row, not also a receipt row (#121).
  - Bridge is one aggregate row however many products need a bridge (#186): "N products need a pack size before their prices compare", or "a bridge" with a breakdown by reason when they differ.
  - A draft that belongs to a failed job is not listed twice (D4).
  - A failure in any kind returns an error, never a partial list (D6).
- **UI-2.12** Home renders the inbox as specified in 10.
  - Each row's action opens the page that resolves it, and resolving an item removes it without a manual refresh.
  - A response that arrives after a resolving mutation does not bring the item back.
- **UI-2.13** The Home nav item shows the count of inbox rows (G6); a batch of products needing a bridge adds one, not one per product (#186). The count is hidden until the first response arrives (G16), and a failed request shows "!" (D6). The separate "Needs a bridge" and "To identify" nav items are gone.
- **UI-2.14** "New purchase" is not a nav item; it is reachable from Capture and as the primary action on Shop pages.
- **UI-2.15** Receipts being read show as "Reading n receipts…" on Home, not counted in the badge (G1). While a batch of several is read, the line reads "Reading 4 of 12 receipts · about 8 minutes left", with no estimate until a read has finished (issue 122). When the oldest one is older than `INGEST_STALL_MINUTES` (default 10), the line reads "Reading is taking longer than usual · Check System" (D21).
- **UI-2.16** The Notice carries confirmations across a navigation (D18).
- **UI-2.17** When `/health` reports a different commit from the one it reported when the tab loaded, the page shows "A new version is ready." with Reload and "Not now". It never reloads by itself, stays across navigation, and shows nothing while either commit is missing or "unknown" (#209).
  - Back and reload do not show it again, and only one shows at a time.
  - An action in it can be reached by keyboard.
  - The shelf-price "Saved" notice clears after 3 seconds.

## UI-3 — Page layouts

- **UI-3.1** Every page uses the header pattern in 10: a Fraunces title, a one-line description, and one primary action top-right.
- **UI-3.2** No create form sits above a list. Products, Ingredients and Vendors create in a right-side drawer, with Cancel and the primary action in a sticky footer.
- **UI-3.3** Drawers keep focus inside, close on Escape and on Cancel, and return focus to the button that opened them. With typed input, Escape, a backdrop click and Cancel show the Keep editing / Discard bar instead of closing, and focus goes to Keep editing (D5).
- **UI-3.4** Products shows search, category filter chips, "Show inactive", and the five-column table in 10. Search and the category filter run on the server and find matches beyond the first page, and rows are ordered by name (D12, T16).
- **UI-3.5a** A product's form offers "Pieces in the pack" and "Piece name" when the pack unit is a weight or volume, and hides them for a pack counted in pieces; packs show as "14 oz · 4 links".
- **UI-3.5** The add product drawer expands the density section with an explanation when the pack unit can't convert to the ingredient's canonical unit. Saving without a density is allowed. A Bridge inbox item appears when the first price for that product is observed (T7).
- **UI-3.6** Ingredients follows the Products pattern, and the category field suggests the nine known categories.
- **UI-3.6a** A category is chosen, not typed: the ingredient forms, the Add product drawer (for an ingredient created there) and a Category card on the product page offer "No category", the nine categories and "Other…" for free text. The product page's card sets the ingredient's category, for every product of it. The products list has a "No category" filter: products whose ingredient has no category key (`GET /products?category=none`).
- **UI-3.6b** The products list has a "Needs a photo" filter, kept in the URL: active products with no main photo (`GET /products?no_photo=true`, also with a search or a category). With a products helper, the page of a branded product with no barcode offers "Search by name" in its "Look this up online" card; a product without a brand does not (#184).
- **UI-3.7** Vendors shows search, the kind segmented control, the list/map toggle in the URL, and the card grid in 10. The map view keeps pin-drop creation (T9).
- **UI-3.8** "Find nearby" opens the OpenStreetMap adoption flow in a dialog. It is pre-set to the only home base when there is one; with no home base, it directs the user to add a kitchen first.
- **UI-3.9** The ingredient hub shows the header, summary strip, prices by vendor and right column in 10.
  - Cards for unbuilt phases (stock, recipes, add to list) are omitted, not shown empty.
  - The 90-day sparkline comes from `GET /api/v1/ingredients/{id}/price-history`, and hides with fewer than two points (D22).
- **UI-3.10** Prices by vendor are sorted by normalized unit price, and describe each vendor's pricing scope as "Same price at every location" or "Price set per location" (T15). Prices that can't be compared sort last with the pack price and a link to add a density, and are excluded from Best recent price (G8).
- **UI-3.11** Every list page has an empty state with one sentence and the relevant action. Filtered-empty and truly-empty states differ (G11).
- **UI-3.12** Receipt review keeps Commit disabled, with its reason, until a location is set. After commit, it shows the committed notice and offers "Next draft" when drafts remain (G9). A held draft opens on the squash "needs a careful look" alert with the gap, its flagged lines first, and Commit enabled as on any draft (#121).
- **UI-3.13** After a drawer save, the new row takes focus if it is visible; otherwise the Notice links to it and that link takes focus (G10).
- **UI-3.14** Vendor import shows the dry-run report before anything is written, and nothing is written if the drawer is cancelled (1F).
- **UI-3.15** A vendor suggestion is never applied without a click. Its source link and its current and proposed values are visible before that click, and evidence text is shown as plain text, never as markup (1F).
- **UI-3.16** Receipts lists uploads by batch, newest first, with the counts Add up, To check and Couldn't read (a zero is a dash) and each receipt's trust badge, store, total and line count. The trust badge pairs a mark with words on Receipts, Purchases and Needs you receipt rows; it is never colour alone (issue 122).
- **UI-3.17** A product merge is confirmed in the page, never by a browser dialog: the panel names both products, says what goes to the kept one, shows any unit warnings before Merge is pressed, and puts focus on Cancel. A merged product's page names and links the product it was merged into, and offers neither merge nor reactivation (#179).
- **UI-3.18** Receipt review's "Correct the lines" edits every line of a draft in one table and saves them in one request: Enter adds a row below, the lines' sum against the printed total updates as you type, Commit waits while the table is open, and typed changes are never dropped without asking (#182).
- **UI-3.19** A voided purchase offers "Restore purchase" at its foot, confirmed in the page with focus on Cancel. Restoring turns the page into review with the Notice "Restored. Commit it to put its prices back in the price book.", and a purchase whose receipt was read again says so with no button (#210).
- **UI-3.20** An ingredient's page offers "Merge into…" and, until it is linked, "Link to standard name", both through the link page's merge panel (preview first, focus on Cancel). A merged ingredient's page names and links the ingredient it was merged into, and offers neither merge, link nor reactivation (#211).
- **UI-3.21** A draft whose receipt the image transcriber could not read (`ocr_fallback`, 04 2O) says "Read from the text scan: the image reader wasn't available." in a quiet neutral line under the header, and nothing else about review changes.

## UI-4 — Phone and tablet

- **UI-4.1** Below 1024px the sidebar is replaced by the five-tab bar in 09, with Capture raised in the centre and the second tab labelled "Purchases" (G19, T18).
- **UI-4.2** All interactive elements on phone and tablet layouts are at least 44×44px. Layouts work from 360px to 1023px wide with no horizontal scroll, which e2e checks at 360, 375, 390 and 1000px, comparing against the configured viewport width.
- **UI-4.3** The phone Home shows the wordmark, the top three inbox items with "See all" expanding in place, and recent purchases (G13, G6).
- **UI-4.4** Capture opens as a bottom sheet with the four modes and the detected or last-used store, labelled as in UI-2.10.
- **UI-4.5** Log a shelf price accepts a barcode or a product name, lists the last five products logged at the store before typing, and shows the matched product, the price field, the on-sale toggle, last paid here and best known (G3). Camera scanning is *deferred* to Phase 6 (S1).
- **UI-4.6** The shelf price field opens a decimal keypad. Save and "Save and scan another" are anchored in the bottom thumb zone and behave as 10 specifies (G4).
- **UI-4.7** An unknown barcode offers to create the product with the barcode pre-filled, then returns to price entry.
- **UI-4.8** *Dormant* until Phase 4 (T4): the at-the-store screen. Editing an item's price there records a shelf price observation, and "Done here" creates a purchase linked to the trip plan.
- **UI-4.9** The More tab lists Shop's other pages, Catalog and Settings, plus Cook and Stock once they are built.
- **UI-4.10** Receipt review on phone shows one line per card, "Needs you" lines first, with 44px suggestion buttons and Commit anchored in the thumb zone (G18).

## UI-5 — Ingredient vocabulary (1G)

Added 2026-09-30 with sub-phase 1G (`03`); layouts and copy are in 10. Decision IDs refer to that day's design review.

- **UI-5.1** The ingredient picker lists an exact name or spelling match first, then other catalog matches, then "From the standard list", then "Create new". A spelling match shows "matches <spelling>", one row per ingredient, and "Create new" is hidden when the typed text equals any name, spelling or standard name (DV9, DV23).
- **UI-5.2** Standard names appear only where the picker may create an ingredient and not in edit mode. Choosing one creates nothing until the form saves, and the chosen field shows "matched <spelling>" or "New, from the standard list" (DV22, DV24).
- **UI-5.3** The link page lists rows to review with Link, Rename…, Skip and "Choose another standard name"; skipped rows fold with Reopen; its finish Notice summarises what was decided and points to USDA suggestions when there are any (DV3–DV5).
- **UI-5.4** Merge appears only when Link or Rename hits another ingredient's name. It opens an inline tomato-tinted panel that names the products moving, any unit change and the prices needing a bridge, offers the survivor with a default, and starts focus on Cancel (DV10, DV15).
- **UI-5.5** After a decision the row leaves, focus moves to the next row's first action, and a polite live region announces what happened and how many remain (DV12).
- **UI-5.6** Needs a bridge shows USDA suggestions as grouped rows: densities as radios, measures as checkboxes, per-ingredient "Accept selected" and Skip, no accept-all, and the release footer. A race offers "Keep it · Replace" with Keep it first (DV8, DV18, DV19).
- **UI-5.7** The Link and USDA inbox rows appear and leave as 03 describes; with no USDA data, the section shows the not-loaded copy and there is no USDA row (DV6, DV7).
- **UI-5.8** Below 1024px, link-page rows stack with 44px action buttons and the count strip stays pinned; every 1G control has a visible focus ring and meets 4.5:1 contrast in both themes (DV11).

## UI-6 — Product ingestion and photos (1H–2N)

Added 2026-10-01 with sub-phases 1H, 1I and 2K–2N (`03`, `04`); layouts and copy are in 10. Decision IDs (PD1–PD21) refer to that day's design review, recorded in `13`.

- **UI-6.1** Proposals appear in Needs you as one aggregate row per kind ("5 products to review", product updates, "4 posted prices changed"), with neutral badges and no image; Review opens the oldest (PD3).
- **UI-6.2** The review page shows the photo first and opens only fields with alternatives or conflicts; with a strong match, no conflicts and an ingredient chosen it collapses to the summary, and Accept stays disabled with its reason while something blocks it (PD1, PD2).
- **UI-6.3** Every review state in 10 shows its words: still reading, photo preparing, superseded, decided earlier, barcode taken, accept failed and nothing read (PD6).
- **UI-6.4** After accept, the next proposal opens with the "Added … · Open it" Notice while any wait; after the last, the product page. Reject shows "Rejected. The capture is kept." with no dialog (PD10).
- **UI-6.5** A full review, including choosing the main photo and an ingredient, completes without a pointing device: Tab order follows the sections, radio groups move with arrow keys, and Accept is Ctrl/Cmd+Enter (PD18).
- **UI-6.6** Below 1024px the review is one column with Reject and Accept anchored at 56px; every control is at least 44px (PD17).
- **UI-6.7** Source badges are neutral, flags and conflicts are squash, the main photo is marked by a neutral border and label, and no new element uses herb except actions (PD13).
- **UI-6.8** A cutout sits on the card surface with `--shadow-cutout` in both themes, and a product without a photo shows the category-letter placeholder (PD14, PD15).
- **UI-6.9** The product page's Photos card offers "Use as main photo", "Hide", "Show hidden (n)", "Cutout | Original" when a cutout exists, and the attribution caption; the Labels card shows each label's read text (PD4, PD16).
- **UI-6.10** The Products table leads each row with a 40px photo or placeholder (PD5).
- **UI-6.11** Capture offers four modes; "Photograph a product" sends up to four photos as one proposal and confirms with the Notice (PD11, PD12).
- **UI-6.12** The reading line counts product work in one sentence and turns squash when stalled; an overdue lookup gets its own line; neither counts in the badge (PD7).
- **UI-6.13** "Look this up online" is absent without a products helper, and shows its waiting, overdue and answered states with one (PD8).
- **UI-6.14** The clip window shows every state in 10, including signing in inside the window, the no-page and no-answer messages, and a failed save explained in the clip's own words (never a photo upload's), and stays open after saving (PD9, PD21).
- **UI-6.15** Settings → Capture offers the draggable link, the address it is tied to and a copy-code fallback; the Add product drawer's address field prefills with the "From the address" hint and counts as typed input (PD19, PD20).
