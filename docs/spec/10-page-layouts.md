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
- **Reading line:** when receipts, product pages or product photos are being read, one line sits above Needs you: "Reading 2 receipts and 1 product page…". It turns squash, "This is taking longer than usual · Check System", when reading has stalled (G1, D21, PD7).
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
  - All excludes voided purchases, and the page description adds "Removed purchases are under Voided".
  - When voided purchases exist, a link under the list, "Show voided ({n})", sets `?status=voided`. The count uses the first page, like Drafts, capped as "50+". That view offers "Back to all".
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
  - Once a location is saved, if the header read a store code that location lacks, a quiet line under the location offers it (1F): "Printed on this receipt:" and the printed line in monospace, with other long numbers shown as ••••, then "Remember {code} for {location}". It is offered only for a code that reads like a store number: printed in the top quarter of the receipt right after a store label ("STORE #0412"), on a line that names no member, card, loyalty, rewards, account or phone, and held by no other branch of the vendor. Saving reads "Remembered {code} for {location}." in place; a failure says why and keeps the button. It is not offered once the purchase is committed.
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

## Removing lines and purchases (#72, #74)

Behaviour is in 04, 2H; this is what the screens say. The server's `removal` preview drives every sentence, so the page never predicts on its own.

**Remove purchase, at the foot of the purchase page.**
- A quiet section after the lines, headed "Remove this purchase". The header keeps Edit and Reopen on committed purchases, and review keeps Commit in its sticky bar.
- The line under the heading depends on `removal.outcome`:
  - void: "It's in the price book, so its {n} price(s) will be voided. This can't be undone yet."
  - delete: "Nothing from it reached the price book, so it will be deleted, along with its receipt photo." Without a photo, the sentence ends at "deleted."
- The trigger is a tomato-text secondary button, "Remove purchase".
- **Confirm:** an inline tomato-tinted panel.
  - It is a `role="group"` labelled by its heading, "Remove the {vendor or location} purchase from {date}?", or "Remove this {date} receipt?" when there is no location. The outcome sentence follows.
  - The buttons are "Remove purchase" (tomato fill) and "Keep it". Opening moves focus to "Keep it", and "Keep it" returns focus to the trigger.
  - While pending, the button reads "Removing…" and both are disabled.
  - An error shows a tomato Alert inside the panel. The panel stays open, and focus moves to the Alert. A 404 on a repeated request counts as success.
- **While reading:** "Remove purchase" is disabled, with the line "You can remove it once it's been read." A `409 still_reading` from a race shows the same sentence.
- **After a delete:** the page replaces itself with Purchases and the Notice "Purchase removed.", adding " Its receipt photo was deleted." when one was. Opening the old link shows "This purchase doesn't exist. It may have been removed." with a link to Purchases, not an empty page.
- **After a void:** the page stays and becomes the voided view, and focus moves to its Notice.

**Voided purchase.**
- Read-only, with a neutral "Voided" badge and no Edit, Reopen, Commit or Remove.
- The first element is an info Notice (`role="status"`): "Removed {date} by {name}. Its {n} prices no longer count in the price book."
- The lines table is read-only, and its Observation column reads "voided".

**Removed lines.**
- **Review:** Delete stays one click for a line that was never recorded. A recorded line asks inline in its row, "Delete line {n}? Its price is voided.", with Delete (tomato) and Keep it. Focus goes to Keep it, and back to the row on cancel. No keyboard shortcut deletes.
- **Manual edit form:** when removed lines include recorded ones, a squash line above Save reads "Saving voids {n} price(s) from removed lines." The Notice after saving reads "Saved. {n} price(s) from removed lines were voided."
- A muted caption, "{n} line(s) removed", sits under the lines table on the committed, voided and review views.

**Receipts.**
- A failed read with no purchase shows a tomato secondary "Remove" on its row. It confirms inline: "Remove this receipt? Its photo is deleted.", with Remove and Keep it.
- Removed receipts don't appear. Uploading one again shows the Notice "This receipt was removed before; it's being read again."

**Price records.** A voided price from a purchase shows "Voided: {reason}" and a link, "Open the purchase". The removal reasons are fixed text ("purchase removed", "line removed") and are not typed, by design.

**Phone (below 1024px).**
- The section starts with a divider and an h2, "Remove this purchase".
- Its bottom padding is at least the sticky Commit bar plus the tab bar, so it scrolls fully clear.
- Its buttons are full-width at 44px and stacked, with "Keep it" below "Remove purchase", nearest the thumb.

## Ingredient hub (Ingredient hub)

The most important detail page; search results and inbox items land here most often.

- **Breadcrumb:** Catalog / Ingredients.
- **Header:** the ingredient name with its category chip, and a meta line of canonical unit and perishability. "Inventory tier" is omitted until Phase 5 (T16). "Log shelf price" is the primary action; "Add to list" is dormant until Phase 4.
- **Summary strip:**
  - **Best recent price:** the lowest normalized unit price in the last 90 days, with product, vendor and date, on an olive-tinted card, because olive marks the cheapest price. Prices that can't be compared are excluded (G8).
  - **Last 90 days:** a sparkline and the min–max range across vendors, from `GET /api/v1/ingredients/{id}/price-history?days=90` (D22). The sparkline hides with fewer than two points.
  - **Stock:** dormant until Phase 5. It is omitted, not shown empty.
- **Prices by vendor** (left, wider):
  - Columns: the vendor with its pricing scope in plain words ("Same price at every location" or "Price set per location", T15), the product, the normalized unit price, and the date seen.
  - Sorted by unit price.
  - Prices that can't be compared yet sort last. They show the pack price and "Can't compare yet · add density", which links to the bridge editor (G8).
  - A link to Compare prices.
- **Right column:** Products as pill links. "Used in" recipes are dormant until Phase 3.
- **Empty:** "No prices yet" with Log shelf price.

## Ingredient vocabulary (1G)

Sub-phase 1G (`03`) adds the ingredient picker's spellings and standard names, a link page, and USDA suggestions on Needs a bridge. Decision IDs in this section (DV1–DV25) refer to the 2026-09-30 design review.

**Words (DV16).** The list is "the standard list"; one entry is a "standard name". Row actions are Link, Rename, Merge (only when needed) and Skip; states are To review, Linked, Merged and Skipped. "Confirm" stays reserved for bridges. Ingredient names are lowercase, except proper nouns.

**Ingredient picker (DV9, DV22–DV24).**
- Rows, in order: an exact name or spelling match, other catalog matches, a "From the standard list" group, then "Create new ingredient “…”".
  - A spelling match shows "matches green onion" and is read aloud as "green onion, another name for scallion".
  - One row per ingredient, however many of its spellings match.
  - Standard names carry their category chip; nothing in the group is blue except the focus ring.
  - The group heading is announced by screen readers.
- "Create new" is hidden when the typed text equals any name, spelling or standard name (DV23).
- Standard names appear only where the picker can create an ingredient, and not when editing a product. Compare and Map never show them.
- Choosing a standard name creates nothing until the form saves (DV22). After a choice, the field shows "scallion · g" with the hint "matched green onion", or "New, from the standard list" (DV24). A `409` or `422` shows at the field.

**Link page** (`/catalog/ingredients/link`; no nav item; DV1, DV3–DV5, DV10–DV17, DV25).
- Header: "Link ingredients to the standard list", with the description "Give each ingredient its standard name so spellings and USDA data line up." A count strip below reads "13 to review · 4 skipped".
- Rows, likely duplicates first. Each row shows the ingredient's name, canonical unit and product count; the suggested standard name ("→ scallion"), or "No standard name fits"; the USDA food it would link to; and the actions Link, Rename… and Skip. "Choose another standard name" opens a search of the standard list only (DV4).
- Rename is an inline field with Save and Cancel. A name another ingredient has shows "scallion already exists · Merge into it" (DV13).
- **Merge (DV10, DV15)** appears only when Link or Rename hits a name another active ingredient has. It opens an inline tomato-tinted panel, like Remove purchase:
  - "Merge green onions into scallion? 3 products move. green onions becomes another spelling."
  - When units differ: "Units differ (each → g): 4 prices will need a weight per bunch."
  - "Keep:" radios for the two ingredients with their product counts, defaulting to the one with more products.
  - Tickable measures to copy, pre-ticked when their units share a dimension.
  - "This can't be undone here." then tomato-text "Merge" and Cancel. Focus starts on Cancel.
- A decided row leaves. Focus moves to the next row's Link, or its search when it has no suggestion, and a polite live region says "Linked green onions to scallion. 12 left." (DV12). Skipped rows fold under "Skipped (4)", each with Reopen (DV3).
- Finish (DV5): an olive Notice, "21 reviewed: 14 linked, 3 merged, 4 skipped.", with "Next: review USDA densities for 9 ingredients" when there are any.
- States (DV17, DV25):
  - Loading: "Loading…".
  - Empty: "Every ingredient is linked or skipped" with Back to Home.
  - A failed action shows an Alert inside its row and keeps the input.
  - With no USDA data, rows omit the USDA line.
- While any ingredients are skipped, the Ingredients list shows "4 ingredients skipped when linking · Review" (DV14).
- Phone, below 1024px (DV11): each row stacks the name line, the "→ scallion" line with the USDA food, then Link, Rename and Skip as a row of 44px buttons. The count strip stays pinned under the header.

**USDA suggestions on Needs a bridge (DV2, DV6–DV8, DV18–DV21).**
- A first section, "USDA suggestions", sits above the existing table on Needs a bridge. There is no separate page.
- Grouped rows, never cards (DV8). Each group shows:
  - the ingredient, its unit and the USDA food it is linked to;
  - the densities as radios, each with its portion, grams and the resulting g/ml;
  - the measures as checkboxes, ticked by default;
  - "Accept selected" and Skip.
- A footer names the source: "Data: USDA FoodData Central, release 2026-04-30".
- There is no accept-all (DV18). A decided ingredient leaves the section.
- Values the ingredient already has are not offered (DV19). An ingredient counted in `each` gets measures only (DV21).
- A race shows "scallion now has a density of 0.52 g/ml (unconfirmed). Keep it · Replace", with Keep it first (DV19).
- After accepting (DV20): the Notice "Saved 3 values for scallion as unconfirmed. Confirm them on its page after checking a label · Open scallion". The ingredient's bridges show a neutral "Unconfirmed" badge.
- With no USDA data loaded, the section says "USDA data isn't loaded yet. Whoever runs this Kitchen ERP can load it with kerp import usda." and no inbox row appears (DV6).

**Setting packs on Needs a bridge (#186).**
- Above the table, a count line: "71 products · 64 need a pack size". It drops as packs are set.
- A row missing a pack offers "Set the pack" (secondary). It opens an editor in a full-width row under the line, with Pack quantity, Pack unit and, for a mass or volume unit, "Pieces (optional)". Save pack is primary; Cancel and Escape close it.
- Saving uses the ordinary product update, which re-prices the product. The Notice says "Saved the pack for Bread Flour.", and the editor for the next product waiting on a pack opens with focus on its quantity, so a batch can be worked through from the keyboard.
- A product whose prices now compare leaves the list. One that now needs something else, such as a density, stays with its new reason.
- Rows missing a density or a measure keep their link to the ingredient page.
- Checks run before saving: "Pack quantity must be a positive number, like 500.", "Choose a pack unit.", and "Pieces must be a whole number, like 5."
- Phone, below 1024px: the editor stays in view while the table scrolls sideways, its fields stack, and its buttons are 44px.

**Inbox rows (DV1, DV7).**
- Badge "Link", title "13 ingredients to link to the standard list", action "Review", while any ingredient is To review.
- Badge "USDA", title "9 ingredients have USDA densities to review", linking to the section above. It appears once no ingredient is To review, or when a linked ingredient has suggestions. The link page's finish Notice points to it.

## Products (Products: search and catalog first · Products: add drawer · Add drawer: unsaved input)

- **Header:** "Add product" is the primary action.
- **Search:** a 48px field matching name, brand, ingredient or barcode. It searches on the server (`GET /api/v1/products?q=`, D12).
- **Filters:** "All" plus category filter chips in category colours, filtered on the server by `category_key` (D12), and a "Show inactive" checkbox at the right.
- **Table card:**
  - Columns: Product (a 40px photo or placeholder, then name over brand; PD5), Ingredient (name and category chip), Pack, Quality, and Last paid (price over vendor and date).
  - Quality shows walnut stars with an accessible label (T13b), or "—" when unrated.
  - Rows are ordered by name.
  - Last paid comes from committed purchases (T16).
- **States (G11):** a filtered-empty list reads "No products match 'oat' in Dairy · Clear filters"; a truly empty list reads "Add your first product".

**Add product drawer.**
- **Fields, in order:**
  - Start from a web address (optional, PD20). Name and item number taken from the address carry the hint "From the address", and a pasted address counts as typed input for the discard rule. With the products helper set up, the Notice after saving reads "Added {name}. Details from the page will arrive in Needs you"; without it, the field says "This page can't be read from here. Use Save to Kitchen ERP on the page."
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

**Product page: photos and labels (PD4, PD16).**
- A Photos card sits right after the meta line. It shows the main photo at 480px (its cutout on the card surface when it has one) and the other photos as thumbnails.
  - Per photo: "Use as main photo" and "Hide". "Show hidden (n)" brings hidden photos back, like "Show voided".
  - "Cutout | Original" is a segmented control, shown only when a cutout exists.
  - "Add photo" (secondary) uploads up to four photos with a role each.
  - Attribution, when a source requires it, is a caption under the photo.
- **Photo states:**
  - A photo being prepared is a neutral tile reading "Preparing photo…".
  - A photo that failed reads "Couldn't process this photo · Retry".
  - No photo at all shows the category placeholder (08) beside "Add photo".
- A Labels card follows, one row per label photo (Front label, Nutrition, Ingredients, Shelf tag) with its read text beneath.
- In Price records, a posted price reads "Posted online, not counted in cheapest".

**Product page: price records (#73).**
- A "Price records" card sits under the prices chart and lists every current price for the product: amount per quantity, location, date, and whether it was a shelf price or came from a purchase.
- **Shelf price:** a "Void" action (tomato, secondary weight) opens an inline form under the row. A reason is required ("Why is it wrong?") and is kept with the voided price. Confirming voids it; it leaves the chart, cheapest and compare, and the original stays for audit.
- **Price from a purchase:** no Void. The row links to "Correct in its purchase", because voiding it on its own would leave the purchase saying the line was observed.
- **Show voided:** a checkbox at the card's right brings back voided prices, struck through, with "Voided: {reason}" and no actions.

**Ingredients** follows the same pattern as Products: search and catalog first, "Add ingredient" in a drawer, and a category field that suggests the nine known categories.

## Product review (2L; PD1, PD2, PD6, PD10, PD13, PD18)

Reached from the inbox at `/catalog/products/review/:id`; no nav item. Laid out like receipt review.

- **Left, sticky:** the evidence. This is the main photo or cutout, then the source address as plain text and the capture's channel and date.
- **Right, one surface, sections divided by space and Fraunces headings (no stacked cards):**
  - **Header:** h1 "New product: {title}" or "Update {product}", with the meta line "From {vendor} · {how it arrived} · {date}".
  - **Match:** neutral radio rows, "Update {product}" (preselected on a strong match) or "Create new product".
  - **Summary:** name, brand, pack and barcode on one line. A field with alternatives or a conflict opens beneath it with its choices as radio rows, each with a neutral source badge; a model's guess carries a squash outline. Identity conflicts are squash, side by side, with no default.
  - **Ingredient:** the ingredient picker (1G).
  - **Images:** photo tiles (08) as a radio group for the main photo, each with a role select and a Hide checkbox.
  - **Kind:** a segmented control: Branded, Store brand, Weighed, Loose, Market stall.
  - **Price:** shown when the capture had one. "Not one of your stores" is a squash outline with a location picker and defaults to recording no price.
- **Collapsed form:** with a strong match, no conflicts and an ingredient chosen, the page shows the summary with Accept and "Edit details".
- **Look this up online:** a secondary action, shown only when a products helper is set up (PD8).
  - After a click it reads "Asked the lookup helper 2 min ago".
  - Past the waiting threshold it turns squash: "No answer yet · Check System".
  - Answers carry the source badge "Lookup helper".
- **Sticky bar:** Reject (secondary) and Accept (primary). Accept stays disabled with its reason while something blocks it ("Choose an ingredient to accept").
- **After deciding:** while other proposals wait, the next oldest opens with the Notice "Added {name} · Open it"; after the last, the product's page. Reject has no confirmation dialog; its Notice reads "Rejected. The capture is kept."
- **States:**
  - **Still reading:** skeleton rows, "Reading this page…".
  - **Photo preparing:** a "Preparing photo…" tile; Accept stays available.
  - **Superseded or no longer pending:** an info Notice, "A newer capture replaced this · Open it", and the page is read-only.
  - **Decided earlier:** read-only, "Accepted Oct 1 · Open {product}".
  - **Barcode taken:** an inline squash panel by the barcode, "Barcode {code} already belongs to {product}", with "Update {product} instead" and "Keep reviewing".
  - **Accept failed:** a tomato Alert above the bar, choices kept.
  - **Nothing read:** "Nothing could be read from this photo. Fill in what you know.", with the fields empty and editable.
- **Keyboard:** Tab follows the sections; Match and the main photo move with the arrow keys; alternatives use the Combobox; Accept is Ctrl/Cmd+Enter or the button; focus starts on Match; the shortcut legend is receipt review's.

## Clip window (2M; PD9, PD21)

A small window, about 420×560, with no app chrome. h1 "Save this product".
- **Shows:**
  - the page title and address as text;
  - how many images came along;
  - the matched vendor, or "Not one of your vendors" with a vendor picker and "Save without a store".
- **Actions:** primary Save, and Cancel.
- **States:**
  - "Waiting for the page…" until the page answers.
  - Signed out: a sign-in form inside the window, then the exchange repeats.
  - "This site blocks clipping. Paste the address in Add product instead."
  - "This page is too large to save."
  - "Already saved · Open it".
  - Success: "Saved · Review it in Needs you". The window stays open until closed.

## Settings: capture (2M; PD19)

`/settings/capture`.
- A draggable link styled as a secondary button, "Save to Kitchen ERP". It is not herb, because clicking it here does nothing.
- One sentence on dragging it to the bookmarks bar.
- The address it is tied to, with "Reinstall if this address changes".
- "Copy the code" for browsers that can't drag (iOS Safari).

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
- **Export and import (1F):** the header gains secondary "Export" and "Import" actions beside "Find nearby".
  - Export opens a small dialog with two sections, each offering YAML (first) and JSON. "Public — {s} of {n} locations shared" says what it holds and what it leaves out (notes, home bases, store codes, stands, and locations neither shared nor linked), and when some are left out, "Link stores to OpenStreetMap or tick Share on a location to include more." "Household" says it holds everything, including store codes, notes and home bases: "Keep it private." Below 1024px it sits behind "More actions" with Find nearby and Import.
  - Import opens a right-side drawer. Choosing a file runs a dry run ("Checking {file}…"), then shows the report top to bottom: the file name and whether it is a Public or Household file; a count strip, "{c} to create · {u} to update · {n} need you · {k} unchanged"; **Needs you**, where a conflict reads "Your edit is kept: {field} ({yours}). The file says {theirs}.", an unmatched row says why and what to do ("Matches 2 locations within 150 m; add a key or link it to OpenStreetMap. Not imported."), and each unknown home base reads "No home base called {name}"; **Will change**, created rows with their fields and updated rows as "{field}: {old} → {new}"; and "{k} unchanged" folded away. Rows are divided lines under these headings, never cards.
  - A refused file shows its reason inside the drawer and stays chosen, under "Choose another file". A file that changes nothing says "This file matches what you have. Nothing to import." and Import stays disabled. After Import, the Notice gives the counts, and says "{n} changed since the preview" when the run differed from it. The drawer follows the unsaved-input rule once a file is chosen; cancelling writes nothing.
  - Below 1024px, Find nearby, Export and Import sit behind one "More actions" button.
- **Vendor suggestions (1F):** with suggestions waiting, a line above the list reads "{n} suggestions from {tool} to review · Review", and a vendor's page reads "{n} suggestions to review · Review". Both, and the inbox row (`/catalog/vendors?suggestions=1`), open one right-side review drawer; there is no review page.
  - The drawer shows one vendor at a time, chosen from a labelled picker ("{vendor} · {n}", largest first), with "From {tool} {version}. Nothing changes until you accept it." Rows sit under the vendor's name (its own facts) and then each location's name, as divided rows.
  - A row shows the field, the proposed value as the app shows it elsewhere (hours in words, pricing in words, an OSM link as "node 123"), what it replaces, the source's domain opening in a new tab (`rel="noopener noreferrer"`), and the tool's evidence as plain text under "Why". The tool's confidence is stored, not shown. Buttons: Accept and Reject, named with field and place ("Accept phone for Elm St").
  - A row whose field changed since it was proposed reads "Changed since proposed", then "Was {old} · Now {current} · Proposed {new}", with "Keep mine" as the main button and "Replace with proposed" beside it. "Accept all for {vendor}" skips such rows ("Changed rows are left for you.").
  - A decided row leaves; focus moves to the next row, and a polite live region says what happened. After a vendor's last row, the next vendor opens. Closing the drawer shows a Notice such as "10 accepted, 2 kept yours, 1 rejected."
- **Location card (1F):** the card stays compact.
  - Its meta line reads hours · phone (a `tel:` link) · address · home base. A line under it says "Linked to OpenStreetMap · Refresh · Unlink", or "Not linked to OpenStreetMap · Link". Unlink forgets the link and keeps every value.
  - "Link" opens a dialog modeled on Find nearby. It lists the OSM places within 250 m of the pin, nearest first, each with its distance, kind and address. A place already linked to another location is shown disabled with "Linked to {name}". Choosing one shows "Will fill: {fields} · Keeps your: {fields} (you edited it)" before "Link" writes anything. The dialog names its states: "Searching near this pin…", "No OpenStreetMap places within 250 m.", and lookups off.
  - A "Sources" disclosure lists where each value came from, in one sentence pattern: "From OpenStreetMap (node 123), checked {date}", "From the file {name}, imported {date}", "Suggested by {tool}, accepted {date}". A value a person changed since then has no source line: it was entered by hand.
  - The edit form has Phone (kept as typed; 7 to 15 digits and only `+ ( ) - .` besides), and shows each field's source as its hint while the field still holds that value.
  - A "Share in public export" checkbox in the edit form sets `publishable` (Phase 1 of 1F). A linked location shows it ticked and disabled, "Shared because it's linked to OpenStreetMap"; a stand shows it unticked and disabled, "Stands are never shared".
- **Find nearby:** opens a dialog containing the OpenStreetMap adoption flow, with its home-base picker pre-set to the only home base when there is one. If there is no home base, the dialog says to add one first and links to Settings → Kitchens.
- **States:**
  - Empty list: "No vendors yet. Drop a pin on the map".
  - Empty map: "No locations yet. Click the map to drop a pin".

## Settings: users and passwords (#75)

**Users (admins only).**
- The members table has an "Edit" button per row, which opens a right-side drawer. The drawer follows 08's unsaved-input rule (D5).
- **Drawer form:** Display name, Email, and Role (Member, or Admin: also manages members and tokens). "Save changes" sends only what changed.
- **Signing in:**
  - "Deactivate {name}" (tomato) asks inline first. The prompt says they are signed out everywhere, that their API tokens stop working, and that reactivating later leaves the tokens revoked.
  - An inactive member shows "Reactivate {name}".
  - "Set a new password" (at least 8 characters) signs them out everywhere; the admin shares it privately.
- **Your own row:** the Role radios are disabled, and there is no Deactivate. The note reads "You can't remove your own admin access. Another admin can." The password section links to Settings, System. The household can never lose its last active admin.
- **After saving:** the drawer closes and the Notice confirms what happened.

**System (everyone).** A "Password" card sits between Status and Sessions: Current password, New password, and New password again. Changing the password keeps this session and signs out every other one. A wrong current password shows an inline error.

## Search palette (Search palette)

- **Container:** a centred dialog, 640px wide, over a scrim.
- **Input:** the search field with an Esc hint.
- **Results:** grouped (Ingredients, Products, Vendors), with the selected result in a neutral tint; herb is kept for actions.
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
- Four 76px option rows, each with an icon tile, title and one-line description: Log a shelf price, Scan a receipt, Enter a purchase, Photograph a product (PD12).

**Photograph a product (PD11, PD12).**
- Opens the camera.
- After the first photo, it offers "Add another photo of this product" (up to four), each with a role.
- Sending shows a determinate upload bar, then the Notice "Photo saved. It'll appear in Needs you once it's identified."
- Errors are inline and keep the photo: "This photo is over 50 megapixels." and "This file isn't a photo we can read."

**Product review (Phone, PD17).**
- One column: a 120px photo band, then Match, the summary, Ingredient, Images (two-column tiles), Kind and Price.
- Alternatives open as full-width radio lists.
- Reject and Accept (56px) are anchored at the bottom.
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
- **"Get here" list:** 28px check circles. Each row's price is a 44px button, and editing it records a shelf price observation; edited prices show a neutral tint.
- **"Cheaper at stop 2":** lists deferred items, with a "Get here" override.
- **Footer:** an anchored "Done here · record purchase" button creates a purchase linked to the trip plan.

These phone screens are also the design brief for the Phase 6 native capture app.
