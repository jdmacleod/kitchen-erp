# Store brands and brand families (2R)

This sub-phase teaches Kitchen ERP which brands are a store's own, whose they are, and where they fit on that store's price ladder. The household approved it for implementation on 2026-10-09, as a Phase 2 sub-phase (PR #290).

## Why

Many of the products a household buys are a store's own brand. A store brand is sold only by its own stores, or by a group of stores under one owner, so it says something a national brand doesn't: "this is the warehouse club's own label" means a different chain is unlikely to sell it. Today Kitchen ERP can't tell the two apart:

- `product.brand` is free text. Two spellings of one brand are two brands, and no brand knows its owner.
- `product.kind` has a `private_label` value, shown as "Store brand", but nothing sets it. Spec 13 §8 says "vendor brand gives `private_label`"; the code never compares a brand with any vendor, so every branded product comes out `branded`.
- Vendors have no parent. Two banners of one company, which sell the same store brands, are unrelated vendors.

The household wants four things:

1. **Labels.** A product says whose house brand it is, or that it is national or unknown.
2. **Narrower matches.** A store's own brand offered as a match for a line from another family's store ranks lower and says why.
3. **Like for like.** An ingredient's offers show each store brand's tier (value, standard, premium, organic), and whether each offer is a store brand or a national one.
4. **Clean brand text.** Two spellings of one brand are recognised as one.

## The dataset

The facts come from **kitchen-erp-brands**, a separate ODbL dataset in the file format `kitchen-erp-brands/1`, kept like kitchen-erp-vendors (1F): one family per file, every fact sourced and dated, checked by its own tools, published as one bundle. This repository ships no real families. Its tests, fixtures and documents use invented ones.

A **family** is a group of stores that sells the same house brands. It is usually one company, but not always: two chains under one owner may keep separate ranges, and a wholesaler or buying cooperative is a family with no stores of its own whose labels many unrelated stores sell. A family has:

- `kind`: `retailer`, `wholesaler` or `cooperative`;
- `banners`: the names on its stores, each with the chain's own Wikidata id (the one OpenStreetMap tags as `brand:wikidata`) and its website domains;
- `brands`: each with a key, a name, aliases, a `tier` (`value`, `standard`, `premium`, `organic`, `natural`, `prepared` or `specialty`), and optional `since`, `until` and `replaced_by`;
- `carries`: the wholesale labels a retailer family sells;
- `gs1_prefixes`: the barcode company prefixes its own-brand products start with, each possibly shared with another family.

A brand the dataset doesn't name is national or unknown. A barcode prefix is evidence for a family, never against one: a store brand is often packed under its maker's own prefix.

Brand names are compared by a **name key**: accents, trademark signs, case, apostrophes and punctuation are dropped, and `&` reads as "and". The rule is written once in `app/catalog/brand_names.py`, pure and property-tested, and matches the dataset's own.

## Data

New tables, all with UUID primary keys:

- `brand_family`: `key` (unique), `name`, `kind`, `wikidata`, `owner_name`, `owner_wikidata`, `sources` (jsonb), `field_source`.
- `brand_family_banner`: `family_id`, `key` (unique), `name`, `wikidata`, `domains` (text[]).
- `brand`: `family_id`, `key` (unique), `name`, `tier` (checked against the list), `categories` (text[]), `since`, `until`, `replaced_by_id` (self-reference), `field_source`.
- `brand_alias`: `brand_id`, `alias`, `alias_key` (unique). The brand's own name is one of its aliases.
- `brand_family_carries`: `family_id`, `brand_id`, unique together.

New nullable columns:

- `vendor.brand_family_id`: which family's stores this vendor's are.
- `product.brand_id`: which brand the product's `brand` text names. `product.brand` stays as printed.

The migration is reversible: downgrade drops the columns, then the tables.

## Import

`kerp import brands --from FILE [--dry-run]` reads a `kitchen-erp-brands/1` bundle through the interchange loader (`services/interchange.py`: safe YAML or JSON, no anchors, no floats, 5 MB) into a strict schema, `schemas/brand_interchange.py`.

- Rows match by key. A field a person has edited keeps the person's value (`write_unless_edited`, 1F).
- A family, banner or brand missing from the file is reported, never deleted, because products point at brands.
- An alias whose key belongs to another brand is refused with the line that holds it, and the rest of the file is still imported.
- The report gives counts of what was added, changed, kept and missing, and lists the vendors and products it linked (below).

## Linking vendors and products

**Vendor to family.** On import, and when a vendor is saved, `vendor.brand_family_id` is filled by the first rule that gives exactly one family:

1. the vendor's `wikidata` is a banner's or a family's id;
2. the host of the vendor's website is one of a banner's domains, or a subdomain of one;
3. the vendor's name or `brand` has the same name key as exactly one banner.

The rule that matched is recorded in `field_source`. A person can set or clear the family on the vendor page; their choice wins. When the name rule finds two banners, nothing is set and the import report lists the vendor as needing a person.

**Product to brand.** `product.brand_id` is filled when a product is saved or a proposal accepted: by the `brand` text's name key, exactly, then by its longest leading run of words that is an alias. Only brand text is looked at, never a title. A retired brand resolves to itself; its `replaced_by` is shown, never substituted. A barcode prefix alone links no brand, but says which family's it is (below).

A catch-up command, `kerp products brand-families --plan` then `--apply`, links existing products and vendors. It skips fields a person has edited, like `kerp ingredients perishability`.

## Kind

A product whose brand belongs to a retailer, wholesaler or cooperative family is `private_label`. This rule:

- applies in `_kind_from_evidence` (`services/catalog.py`), the default on accepting a proposal (`services/proposals.py`), and the preselected kind on the product review page;
- beats `unbranded_vendor`: a market's own label with a resolved house brand is a store brand, and "Market stall" stays for products with no brand at all;
- never changes a kind a person chose.

A product with a barcode and no house brand stays `branded`. A barcode whose prefix belongs to a family, with no brand text, is offered as `private_label` for that family and left for the person to confirm.

## Matching

**Out of family.** When the vendor of a receipt line or capture has a family, a candidate product whose brand belongs to a different retailer family:

- ranks below every in-family and national candidate of the same score, in `match_catalog` (`services/proposals.py`) and the receipt line shortlist (`services/resolution.py`);
- carries `brand_note: "out_of_family"`, shown as "{brand} is {family}'s own brand";
- is never removed. A person can still choose it: stores resell, and the dataset can be stale.

A wholesale label is never out of family. A vendor with no family, or a product with no brand link, is unaffected. Matches by identifier or listing are unaffected.

**Same brand.** `sameness.compare` treats two products whose brands link to the same `brand` row as the same brand, whatever their spelling. The link is passed in, so the function stays pure. A retired brand and the brand that replaced it are different brands.

**Updates.** `lookups._changes_product` compares brands by link when both sides have one, so a respelled brand no longer opens a "Product update". The products helper may then send canonical names, not only spelling fixes.

## Where it shows

- **Product page and lists.** A chip beside the brand: "Store brand · {family}" with its tier, or nothing for a national brand. A retired brand adds "now {replacement}".
- **Ingredient offers and compare** (`services/pricebook_views.py`, `ingredient_offers` and `compare`): each row gains `brand_tier`, `brand_family_name` and `is_store_brand`. The table shows the chip, and a filter offers all, store brands only, or national brands only.
- **Vendor page.** The family, with how it was matched, and a control to change it.
- **Settings, data.** The imported dataset's commit and date, and the last import report.

## Privacy

Real families and brands exist only in the dataset and in a household's imported database. Code, tests, fixtures, comments and commit messages here use invented ones (SECURITY.md). A household that adds its stores' names to its local denylist has the existing PII scan catch a slip.

## Slices

- **2R-1.** Tables, import, vendor and product links, the kind rule, the catch-up command.
- **2R-2.** Out-of-family ranking and its note, brand links in `sameness`, and link-based comparison in `_changes_product`.
- **2R-3.** Tiers and store-brand chips on offers and compare, the filter, and barcode prefixes (a `gs1_prefix` table and the family-only rule).

## Acceptance criteria

R1. Importing an invented bundle twice changes nothing the second time, and a field a person edited between the two imports keeps the person's value.
R2. A family missing from a later bundle is reported and not deleted; products linked to its brands keep their links.
R3. An alias that collides with another brand's is refused by line, and the rest of the bundle imports.
R4. A vendor links to its family by Wikidata id, by website domain or by an unambiguous name, in that order, and an ambiguous name links nothing and is reported.
R5. "LARKSPUR SELECT", "Larkspur Select®" and "Larkspur Select" link to one brand; a title containing the name links nothing.
R6. A product whose brand links to a retailer family is `private_label`, also when it has an exclusive vendor; a product with only a barcode and no house brand is `branded`; a kind a person chose is never changed.
R7. On a receipt from a vendor in one family, another retailer family's store brand appears in the shortlist below in-family and national candidates of the same score, with its note; a wholesale label appears with no note.
R8. Two products with differently spelled brands that link to one brand compare as the same brand; a retired brand and its replacement do not.
R9. A respelled brand from the helper opens no "Product update" when both spellings link to one brand.
R10. An ingredient's offers show each store brand's tier and family, and the store-brand and national filters each show only their rows.
R11. The migration upgrades and downgrades cleanly, and the import path holds no float.

## Open questions

- Should the household be able to add a brand of its own (a local shop's label the dataset lacks) in the app, or only through the dataset?
- Should a store brand's tier ever come from the household rather than the dataset, for a brand the dataset places wrongly?
