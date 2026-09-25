# 10 — Page layouts

This document describes the approved layout of each page. The design canvas (see 08) shows each one as an artboard, named in parentheses. Decision IDs refer to the 2026-09-25 review record.

## Desktop page pattern

Every page follows the same hierarchy:

1. **Header**: an `h1` title in Fraunces, a one-line plain-language description in `neutral-600`, and a single primary action top-right, with secondary actions to its left.
2. **The thing you came for**: search plus the list, table or detail content, taking most of the space.
3. **Creation moves out of the page flow** into a right-side drawer opened by the primary action. Forms never sit above the list they add to.

Main content is left-aligned beside the sidebar with 44–56px padding. List pages may run full width; reading-heavy pages cap around 920px. Confirmations use the shared Notice at the top of the main content (D18).

## Home (Home: unified inbox · Home: first run · Home: inbox loading, empty, error)

- **Header:** a time-of-day greeting without a name ("Good afternoon") and a one-line summary, "3 things need you" or "Nothing needs you" (G12).
- **Reading line:** when receipts are being read, "Reading n receipts…" sits above Needs you. It turns squash with "Check System" when reading has stalled (G1, D21).
- **Layout:** two columns, roughly 1.65 : 1.
- **Left, Needs you:** one card of inbox rows, oldest first.
  - Each row is a three-column grid: a kind badge, the title with a one-line explanation, and a soft action button.
  - Kind badges are neutral pills, except "Couldn't read" in the tomato tint (G5).
  - Aggregate rows carry their size in the title (G6).
  - A footer line explains what the inbox is.
- **Right column:** Recent purchases. The Shopping list panel is dormant until Phase 4.
- **States:**
  - Loading: skeleton rows, with the badge hidden.
  - Empty: "Nothing needs you", a sentence of context, and a link to Capture.
  - Error: a tomato alert "Couldn't load what needs you" with Try again; the nav badge shows "!" (D6).
- **First run (T11, G7):** until the first committed purchase, Home shows "Set up your kitchen" with two numbered steps:
  1. Drop a pin where you shop (open the Vendors map view).
  2. Record what you bought.

  Whenever the inbox has rows, they render above the checklist.

## Shop: purchases (Shop: purchases)

- **Header:** "Purchases", with secondary "Scan a receipt" and primary "New purchase" (UI-2.14).
- **Filter:** a status segmented control: All, Drafts (with a count), Reviewed, Committed. Newest first.
- **Table:** Date, Where, From (Receipt / By hand), Lines, Total and Status.
  - A draft without a location reads "Location needed" in squash.
  - Receipts being read appear as rows with a Reading pill (G1).
- **Empty:** "No purchases yet" with Scan a receipt and New purchase.

## Shop: receipt review (Shop: receipt review)

- **Breadcrumb:** Shop / Purchases.
- **Header:**
  - Title "Receipt, {date}" with its status badge.
  - Meta line: lines read, the receipt total, and how many lines are matched.
  - Primary: "Commit purchase". It stays disabled with a reason ("Choose where you shopped to commit") until a location is set.
- **Where you shopped:** location candidates read from the receipt as chips, plus "Somewhere else…".
- **Lines:**
  - A segmented filter: "Needs you n" and "All n".
  - Columns: what the receipt says (monospace), what it was read as (quantity × price), and the product. The product column shows either suggestions as soft buttons or "Choose a product" / "Ignore line".
  - The keyboard shortcuts are listed under the table.
  - A line that fails to save shows its error inline and keeps the input.
- **After commit (G9):** the page stays and turns Committed, with the Notice "Committed. n prices added to the price book." If other drafts remain, "Next draft" becomes the primary action.
- **Phone (G18):**
  - One line per card: receipt text, what it was read as, and suggestion buttons at 44px.
  - "Needs you" lines come first.
  - Commit is anchored in the thumb zone.
  - The keyboard shortcuts are hidden.

## Ingredient hub (Ingredient hub)

The most important detail page; search results and inbox items land here most often.

- **Breadcrumb:** Catalog / Ingredients.
- **Header:** the ingredient name with its category chip, and a meta line of canonical unit and perishability. "Inventory tier" is omitted until Phase 5 (T16). "Log shelf price" is the primary action; "Add to list" is dormant until Phase 4.
- **Summary strip:**
  - **Best recent price:** the lowest normalized unit price in the last 90 days, with product, vendor and date, on an herb-tinted card. Prices that can't be compared are excluded (G8).
  - **Last 90 days:** a sparkline and the min–max range across vendors, from `GET /api/v1/ingredients/{id}/price-history?days=90` (D22). The sparkline hides with fewer than two points.
  - **Stock:** dormant until Phase 5. It is omitted, not shown empty.
- **Prices by vendor** (left, wider):
  - Columns: the vendor with its pricing scope in plain words ("Same price at every location" or "Price set per location", T15), the product, the normalized unit price, and the date seen.
  - Sorted by unit price.
  - Prices that can't be compared yet sort last. They show the pack price and "Can't compare yet · add density", which links to the bridge editor (G8).
  - A link to Compare prices.
- **Right column:** Products as pill links. "Used in" recipes are dormant until Phase 3.
- **Empty:** "No prices yet" with Log shelf price.

## Products (Products: search and catalog first · Products: add drawer · Add drawer: unsaved input)

- **Header:** "Add product" is the primary action.
- **Search:** a 48px field matching name, brand, ingredient or barcode. It searches on the server (`GET /api/v1/products?q=`, D12).
- **Filters:** "All" plus category filter chips in category colours, filtered on the server by `category_key` (D12), and a "Show inactive" checkbox at the right.
- **Table card:**
  - Columns: Product (name over brand), Ingredient (name and category chip), Pack, Quality, and Last paid (price over vendor and date).
  - Quality shows walnut stars with an accessible label (T13b), or "—" when unrated.
  - Rows are ordered by name.
  - Last paid comes from committed purchases (T16).
- **States (G11):** a filtered-empty list reads "No products match 'oat' in Dairy · Clear filters"; a truly empty list reads "Add your first product".

**Add product drawer.**
- **Fields, in order:**
  - Ingredient: search, with the hint "no match creates a new ingredient".
  - Brand and Name, side by side.
  - Pack size: quantity plus unit.
  - Barcode (optional).
  - Quality: None plus five star toggle buttons.
  - Notes.
  - A collapsed "Density override" section.
- **Footer:** Cancel and "Add product".
- **Density:** when the chosen pack unit can't convert to the ingredient's canonical unit, the density section expands and says why. Saving without a density is still allowed; a Bridge inbox item appears once a price is observed for the product (T7).
- **Unsaved input:** the drawer follows 08's rule (D5).
- **After saving:**
  - If the new row is visible, it takes focus.
  - Otherwise the Notice reads "Added {name} · Open it", with focus on the link (G10).

**Ingredients** follows the same pattern as Products: search and catalog first, "Add ingredient" in a drawer, and a category field that suggests the nine known categories.

## Vendors (Vendors · Vendors: map view)

- **Header:** secondary "Find nearby" and primary "Add vendor".
- **Controls:** a search field, a segmented control for kind (All, Chains, Independents, Markets, Stands), and a List/Map toggle kept in the URL (`?view=map`, T9).
- **List view:** a three-column grid of cards.
  - Each card shows the name in Fraunces, a kind pill, the number of locations, the last visit (from committed purchases, T16), and the pricing scope in plain words (T15).
- **Map view:**
  - Walnut pins that keep their kind shapes; the selected pin is herb (T13).
  - A shape legend, with the hint "Click the map to drop a pin".
  - The selected location's card at the top right.
  - All current map functions remain, including dropping a pin to create a vendor and its location.
- **Find nearby:** opens a dialog containing the OpenStreetMap adoption flow, with its home-base picker pre-set to the only home base when there is one. If there is no home base, the dialog says to add one first and links to Settings → Kitchens.
- **States:**
  - Empty list: "No vendors yet. Drop a pin on the map".
  - Empty map: "No locations yet. Click the map to drop a pin".

## Search palette (Search palette)

- **Container:** a centred dialog, 640px wide, over a scrim.
- **Input:** the search field with an Esc hint.
- **Results:** grouped (Ingredients, Products, Vendors), with the selected result in an herb tint.
- **Footer:** keyboard hints.
- **States:**
  - Before typing: device recents or the hint (G15).
  - No results: "No matches for '…'" with Add product.
  - Error: "Search isn't working right now" with Try again.

## Sidebar (Sidebar)

As specified in 09. Visual details:
- Oat background with a `neutral-200` right border.
- The active section is an herb-tinted pill in weight 600; the active sub-page is a warm-white pill.
- Count badges are squash.
- There is no kitchen switcher until Phase 4 (S3).

## Phone

All phone screens are 390px wide at design time and must work from 360px. Leave the top safe area clear; do not draw a status bar. The tablet range (up to 1023px) uses the same shell (G19).

**Tab bar (Phone tab bar).** 84px including the home-indicator area. Five tabs as specified in 09; Capture is a 56px herb circle raised above the bar.

**Home (Phone: home).**
- The "Kitchen ERP" wordmark top left, where the kitchen pill used to be (G13), and the avatar top right.
- The greeting title.
- The top three inbox items as 64px tappable rows, with "See all" expanding the list in place (G6).
- Recent purchases (three rows) in place of the list summary card.

**Purchases (Phone: purchases).** A drafts banner ("2 drafts to finish") above the purchase list; each row shows where, the date and line count, the total and a status pill.

**Capture sheet (Phone: capture sheet).**
- A bottom sheet over a scrim with a drag handle and the title "Capture".
- The detected store: "Near {vendor}", or "Last used: {vendor} · change" in a squash outline when location is unavailable (G2).
- Three 76px option rows, each with an icon tile, title and one-line description: Log a shelf price, Scan a receipt, Enter a purchase.
- Cancel at the bottom.

**Shelf price (Phone: shelf price).**
- **Header:** a back button and the title.
- **Store:** shown as a tappable chip, labelled as a guess when it is one (G2).
- **Entry (G3):** one field accepts a barcode or a product name. Before typing, it lists the last five products logged at this store. Camera scanning is deferred (S1).
- **Price:** the matched product card, one large price field (36px Fraunces digits, decimal keypad) and an "On sale" checkbox.
- **Context card:** "Last paid here" and "Best known".
- **Footer (thumb reach):** "Save price" (56px) and "Save and scan another".
- **After saving (G4):**
  - "Save and scan another" keeps the store, clears product, price and On sale, returns focus to the entry field, and shows "Saved $X at {vendor}" for 3 seconds.
  - Plain Save returns to where Capture was opened.
- **Unknown barcode:** offer to create the product with the barcode pre-filled, in the same flow.
- **Lookup states:** pending shows a spinner in the field; an error shows inline with Retry.

**At the store (Phone: at the store).** Dormant until Phase 4 (T4; see TODOS.md). This screen is one stop of a trip plan:
- **Header:** "Stop 1 of 2 · {kitchen} list", the store name, basket progress and the estimated cost here.
- **"Get here" list:** 28px check circles. Each row's price is a 44px button, and editing it records a shelf price observation; edited prices show an herb tint.
- **"Cheaper at stop 2":** lists deferred items, with a "Get here" override.
- **Footer:** an anchored "Done here · record purchase" button creates a purchase linked to the trip plan.

These phone screens are also the design brief for the Phase 6 native capture app.
