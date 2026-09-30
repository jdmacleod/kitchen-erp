# 12 — Ingredient Vocabulary: Overview & Decisions

**Status: draft for review, 2026-09-30. Not approved for implementation; `CLAUDE.md` names Phases 1 and 2 as the approved scope.** Documents 12–15 arrived as a handoff from a design session and are kept close to how they arrived; their tensions with documents 00–11 are being settled in planning.

Handoff from a claude.ai design session. This package covers the **canonical ingredient vocabulary**: seeding it from USDA FoodData Central (FDC), conforming existing Cooklang recipes to it, and suggesting canonical names while authoring.

Read in order: **12** (this: why and what) → **13** (data model and lexicon format) → **14** (FDC import, resolver, conform pass) → **15** (task plan with acceptance criteria).

## Package contents

| path | what |
|---|---|
| `docs/spec/12–15-*.md` | this spec |
| `tools/fdc_profile.py` | stdlib-only profiler that reads the FDC zip directly; v2 (column-name fixes). Produces `fdc_profile.md` and `fdc_candidates.csv`. Use it as reference code for the real importer. |
| `docs/spec/assets/tier-a-ingredients.csv` | draft of 146 canonical ingredients plus 7 abstract parents, with FDC and FoodOn references. **Draft: needs review** (see §6). |

The FDC source file is `FoodData_Central_csv_2026-04-30.zip` (the full CSV download, 481 MB zipped). Like every download, it lives under the gitignored `data/usda/` and is never committed; the importer takes its path as an argument.

## 1. Goal

Maintain one standardized, de-duplicated vocabulary of household ingredients.

- **Seed it from a reputable source.** USDA FDC is public domain and provides stable IDs, nutrients and gram weights.
- **Run a conform pass** over existing recipes in the adjacent `cooklang-recipes` repo so they use canonical names.
- **Stop name drift at authoring time** through autocomplete, suggestions and a lint check.

## 2. Core decision: USDA gives identity, not names

FDC descriptions are inverted, comma-delimited and loaded with cooking state, for example "Onions, spring or scallions (includes tops and bulb), raw". Those strings would render inside recipes, so the vocabulary is layered:

- **Ingredient** (ours): canonical culinary name + slug, optional parent, category, default density.
- **IngredientAlias**: many-to-one synonyms, plurals and misspellings. This is the anti-drift mechanism.
- **IngredientRef**: zero or more external IDs (FDC `fdc_id`, FoodOn, optionally Open Food Facts and Wikidata). One is flagged preferred and supplies nutrition and density.
- **Product** (existing ERP entity): the brand-level purchasable item, e.g. DeCecco spaghetti, linked to an Ingredient. Quality and brand distinctions live here, never in the Ingredient vocabulary.

**Name precedence when seeding:** curated name > FoodOn name > USDA "Common Name" attribute > reworded FDC description.

## 3. Naming rules

These are the rules the resolver, the LLM prompts and human review all enforce.

1. **Culinary word order, lowercase.** Write "yellow onion", "ground cumin", "parmesan". Proper nouns keep their capitals (Parmigiano-Reggiano, Italian sausage).
2. **Use the natural recipe form of the noun.** That is the singular for count nouns (egg, onion, carrot). Use the plural only where a cook naturally writes it (green beans, strawberries, almonds). Other inflections are registered as aliases, and they are **not** drift (see doc 13 §3).
3. **Form that changes what you buy is a separate ingredient.** Garlic vs garlic powder; whole vs ground cumin; dried vs canned beans; salted vs unsalted butter.
4. **Prep you do goes in the note, not the name.** Write `@garlic{3%cloves}(minced)`, not `@minced garlic{…}`.
5. **Cooking state is not identity.** Raw vs cooked is a nutrition concern. The FDC reference should point at the raw or dry record, meaning what you buy and weigh.
6. **Use hierarchy for specificity.** Examples: `cheese` → `parmesan` → `Parmigiano-Reggiano`, and `chicken` → `chicken thigh`. A recipe can name either level, and a Product satisfies an ingredient through the parent chain. This gives substitution and COGS rollups for free.
7. **Brands never appear in Ingredient names.** "TABASCO" becomes `hot sauce` at the Ingredient level, with the brand as a Product.

## 4. Findings from profiling the 2026-04-30 FDC release

These are real numbers from `fdc_profile.py` v2. Use them as acceptance checks for the importer.

**Data types**
- Foundation: 469
- SR Legacy: 7,793
- FNDDS survey foods: 5,432
- Branded: 1,999,950 (all have `gtin_upc`)
- Experimental: 114
- Foundation supporting rows (`sub_sample_food`, `market_acquistion` [sic], `sample_food`, `agricultural_acquisition`): these are not foods for our purposes.

**Recipe-usage signal.** FNDDS defines survey foods as recipes of SR/Foundation inputs, via `input_food.csv`. Counting how often each food appears as an input ranks how "ingredient-like" it is.
- 1,674 SR foods are used at least once; 431 are used at least 5 times; 111 at least 20 times.
- 38 Foundation foods are used.
- 624 inputs are FNDDS-only ingredient codes with no FDC record.

**Noise in that signal:**
- Cooked-state variants rank separately. "Onions, raw" has 200 uses and "Onions, cooked, boiled…" has 111, so they must be collapsed.
- The ranking reflects the average American diet. Gerber water, Nestlé syrup and condensed soup all rank.
- FNDDS codes include nutrient pseudo-ingredients ("Vitamin D as ingredient", "Iron as ingredient", "Folic acid", "Fiber") and composites ("Cheese as ingredient in sandwiches"). Exclude these.
- Four FNDDS-only codes are real staples and need manual references: Vegetable oil NFS (868 uses), Milk NFS (202), Olive oil (69), Mushrooms raw (20).

**Categories to exclude from seeding:** Baby Foods, Fast Foods, Restaurant Foods, Meals/Entrees/Side Dishes, and American Indian/Alaska Native Foods (0 used). Beef has 954 SR rows, of which only 9 are used 5 or more times. Most of SR is noise for a home-kitchen vocabulary.

**FoodOn is in the CSVs,** as rows in `food_attribute.csv` rather than as a column.
- Attribute names include "FoodOn Ontology ID / Name For FDC Item" (Foundation) and "FoodOn Ontology ID / Name #1 [#2] For FDC Item" (SR).
- About 4,126 SR foods and all Foundation foods have one.
- The same rows also carry NCBI Taxon, NCBI Taxon Parent and Scientific Name.
- The USDA "Common Name" attribute (attribute type "Common Name", value in the `value` column) exists on 1,074 SR foods. It is a good source of synonyms.
- FoodOn labels are sometimes wrong: cornstarch → "corn flour", bread flour → "white bread", lemon juice from concentrate → "lime juice". Honey carries an UBERON ID. Never accept FoodOn names without review.

**Portions / density.**
- 7,649 of 8,262 core foods have `food_portion` rows, including 1,661 of the 1,674 FNDDS-used SR foods.
- `measure_unit` is almost always "undetermined". The real unit is free text in `modifier` ("cup, chopped", "tbsp", "medium", "slice", "cup (8 fl oz)"), so it needs a parser (doc 14 §2).

**Branded.**
- 2.0M rows, 449 `branded_food_category` values, and a 954 MB CSV that is mostly label ingredient text.
- Import only a slim lookup table for barcode resolution.
- The categories read like store aisles, which suits the aisle axis rather than the ingredient taxonomy.

## 5. External sources considered

| source | role | license |
|---|---|---|
| USDA FDC | identity, nutrients, density, barcode lookup | public domain |
| FoodOn (via FDC attributes) | naming seed and hierarchy hints, stored as IngredientRef | CC BY 4.0 |
| Open Food Facts ingredients taxonomy | optional synonym source (canonical plus synonyms, DAG); packaged-label oriented | ODbL (share-alike if seed data is ever published) |
| `ingredient-parser` (PyPI) | parses recipe lines into amount, unit, name, prep; optional FDC matching (`foundation_foods=True`, about 20x slower; fine for batch) | MIT |
| Mealie seed foods (cloned repo) | optional: grep for household-scale English vocabulary | AGPL; use as reference only |

## 6. Status of the Tier A seed (`docs/spec/assets/tier-a-ingredients.csv`)

The seed has 146 concrete ingredients plus 7 abstract parents: butter, milk, cheese, chicken, vinegar, bell pepper, cooking oil. The cut-off was about 9 or more combined FNDDS uses, after collapsing variants.

The columns are:
- `slug`, `name`, `parent`
- `fdc_category`, `fdc_id`, `foodon_id`
- `collapsed_fdc_ids`, `fndds_uses`
- `review_note`

**Known review items:**
- **Cooked references.** Many references point at cooked FDC records, because FNDDS models food as eaten: ground beef, chicken parts, pork loin, rice, pasta, dried beans, bacon. Swap each to the raw or dry sibling record. The `review_note` column flags every one.
- **No FDC reference yet:** vegetable oil, olive oil, mushroom. Milk NFS maps to the `milk` parent.
- **Wrong FoodOn labels** are noted per row.
- **Pending identity decisions for the household** (keep them as open questions in the Home inbox or a TODO; don't decide unilaterally):
  - Split dried vs canned beans?
  - Model lemon zest and lemon juice as parts of `lemon`?
  - Include spirits and coffee (dropped from Tier A)?
  - Toasted vs plain sesame oil?

## 7. Sequencing principle

**The household's own recipes define the long tail; USDA supplies the IDs.**
1. Run the extraction over `cooklang-recipes` early (task T6).
2. Intersect that vocabulary with Tier A. This confirms which staples to seed.
3. The unmatched names are the "gourmet gap" (guanciale, fish sauce, Bomba, and so on). Each one gets a canonical entry and an FDC reference through the review queue.

Tier C (all FNDDS-used foods, and SR in general) is **not** seeded. It is the lookup pool the resolver searches.
