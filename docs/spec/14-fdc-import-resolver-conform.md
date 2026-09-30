# 14 — FDC Import, Resolver, Conform Pass & Authoring Suggestions

**Status: draft for review, 2026-09-30. Not approved for implementation; `CLAUDE.md` names Phases 1 and 2 as the approved scope.** Documents 12–15 arrived as a handoff from a design session and are kept close to how they arrived; their tensions with documents 00–11 are being settled in planning.

## 1. FDC importer

The importer reads the zip directly, as `tools/fdc_profile.py` does. It is stdlib `zipfile` + `csv` streaming, so there is no extraction step.

**Arguments:**
- a `--zip` path;
- an optional `--with-branded` flag, because the branded table takes several minutes to load.

**Behaviour:**
- Idempotent: truncate and reload the staging tables inside one transaction.
- Record the release date taken from the filename in an `fdc_release` row.

### Files used and gotchas

| file | use | gotcha |
|---|---|---|
| `food.csv` | core rows | Keep `data_type` in (`foundation_food`, `sr_legacy_food`, `survey_fndds_food`). The data type `market_acquistion` is misspelled in the source; ignore it anyway. |
| `food_category.csv` | category names | `food_category_id` in `food.csv` → `food_category.id` (SR/Foundation groups only). |
| `sr_legacy_food.csv` | `NDB_number` | Needed to resolve `input_food.sr_code`. |
| `input_food.csv` | FNDDS usage counts | The column is **`fdc_of_input_food`**, not `fdc_id_of_input_food`. It is usually empty for SR inputs, so fall back to `sr_code` → NDB → fdc_id. Count only rows whose parent `fdc_id` is `survey_fndds_food`. |
| `fndds_ingredient_nutrient_value.csv` | resolves FNDDS ingredient codes | Headers contain spaces (`ingredient code`, `Ingredient description`, `FDC ID`). The `FDC ID` often points at non-core rows or is blank; 624 codes stay unresolved. Store them as pseudo-rows. |
| `food_attribute.csv` + `food_attribute_type.csv` | FoodOn, common name, scientific name | FoodOn rows have **blank** `food_attribute_type_id` and are keyed by `name` prefix: `FoodOn Ontology ID…` and `FoodOn Ontology Name…` (SR uses `#1`/`#2`). The ID value is a full PURL; keep the trailing `FOODON_…`. "Common Name" rows have type "Common Name", a blank `name`, and the text in `value`. |
| `food_portion.csv` + `measure_unit.csv` | density | `measure_unit` is mostly `undetermined`; the unit is in `modifier`. See §2. |
| `branded_food.csv` | barcode lookup (optional) | 954 MB. Skip the `ingredients` column. `gtin_upc` is present on every row. |

**Acceptance counts** (2026-04-30 release): foundation 469, SR 7,793, FNDDS 5,432; FNDDS-used SR foods 1,674; FNDDS-only codes 624; core foods with at least one portion row 7,649 of 8,262.

## 2. Portion unit parser → density

Parse `modifier` (falling back to `portion_description`) into:
- `unit_norm`: `cup | tbsp | tsp | fl_oz | oz | lb | g | count | slice | clove | …`
- a descriptor: `chopped | shredded | sliced | packed | whole | …`

Map spellings: "tablespoon"/"tbsp", "cup (8 fl oz)"/"cup (1 NLEA serving)" → `cup`.

Compute `density_g_per_ml` from volume units only:
- `gram_weight / (amount × ml_per_unit)`, using the US customary volumes defined in the existing units/conversions layer.
- Prefer the undescribed "cup"; otherwise take the median of the volume portions.

Keep count units ("medium", "large", "clove", "slice") as per-ingredient **count→gram** conversions. They feed the existing unit conversion model and are distinct from density.

Descriptor matters: "cup, chopped" onion ≠ "cup, sliced". Store the descriptor; the recipe-line note ("chopped") can select the matching conversion later. For v1, default to the undescribed conversion.

## 3. Resolver (one service, used by everything)

**Input:** a raw ingredient string, plus optional context (recipe line, note).
**Output:** a ranked list of candidates, each with `{ingredient_id | proposal, confidence, method, moved_to_note?}`.

The cascade stops at the first confident tier:

1. **Exact alias.** `alias_norm` lookup, which includes canonical names and inflections. Confidence 1.0.
2. **Prep-strip.** Remove leading or trailing prep words (minced, chopped, diced, sliced, grated, fresh*, finely, roughly, peeled, softened, melted, divided, packed, to taste, …) and retry step 1. A match returns `moved_to_note` with the stripped words.
   - Do **not** strip form words that change identity: ground, dried, powder, canned, frozen, smoked, toasted, salted/unsalted. "Fresh" is only strippable when a dried sibling doesn't exist or the recipe context is ambiguous. Maintain the list as data.
3. **Fuzzy lexical.** Trigram/Levenshtein over `alias_norm` (Postgres `pg_trgm`). Accept automatically only at ≥ 0.9 **and** a unique top result; otherwise pass the candidates down.
4. **FDC pool match.** Match against `fdc_food` descriptions, FoodOn names and common names. Optionally use `ingredient-parser` with `foundation_foods=True` for batch runs. The output is a *proposal* for a new ingredient with a suggested FDC reference, and never auto-accepted.
5. **Local LLM (Ollama on the server).** Given the string, the top candidates from steps 3–4 and the naming rules (doc 12 §3), return JSON: `{action: map|alias|new, target_slug?, proposed_name?, parent_slug?, fdc_id?, rationale}`. Always goes to review.

**Auto-apply policy:**
- Tiers 1–2 apply automatically.
- Tier 3 applies automatically only when above threshold with a unique top result.
- Everything else creates an inbox item.

Thresholds are config values. Log every decision with its method, so thresholds can be tuned from real data.

## 4. Conform pass (existing recipes)

This is a CLI, which can also run as a job, e.g. `kitchen-erp conform [--apply] [--path …]`. It runs in three stages.

1. **Extract.** Parse every `.cook` file in the `cooklang-recipes` working tree and collect ingredient tokens with source spans: `{file, line, span, raw_name, qty, unit, note}`.
2. **Resolve.** Run the §3 cascade on each distinct `raw_name`; cache results per name.
3. **Report.** Write `conform-report.md`/JSON with these sections:
   - already conformant (including inflections);
   - auto-resolvable (drift with a confident target);
   - needs review;
   - unknown.

   Each entry lists occurrence counts and files. The unknown bucket is the "gourmet gap".

**Review.** Needs-review and unknown names become **Home inbox** items. Each offers: map to existing, add as alias (keep the text), create new ingredient (prefilled from the LLM or FDC proposal), or ignore.

**Apply** (`--apply`):
- Rewrite only drift tokens in the working tree (doc 13 §6 rules).
- Append accepted aliases and new ingredients to `lexicon/ingredients.yaml`.
- **Never commit.** A person reviews `git diff` in `cooklang-recipes` and commits. Git is the undo. The indexer's dirty marker already surfaces the pending state.

Decision persistence:
- An "add as alias" decision means future runs treat that text as conformant.
- An "ignore" decision is stored as a lexicon-side `ignore:` list so it isn't asked again.

## 5. Authoring suggestions (new recipes)

- **Autocomplete API** (`GET /ingredients/suggest?q=`). Prefix plus trigram over `alias_norm`. Returns canonical name, slug, parent chain and the matched alias. Always display the canonical name, e.g. "green onion → scallion".
- **Resolve-on-save.** When the ERP indexes a changed recipe, it resolves new names. Unknowns become inbox items, and nothing is blocked.
- **Lint.** `kitchen-erp lint-recipes` checks any `.cook` file for ingredient names that aren't canonical or alias, and suggests the canonical name. Wire it as an optional pre-commit hook in `cooklang-recipes`. It is advisory by default, with a `--strict` exit code.
- **Editor integration is out of scope for this package.** The same `suggest` endpoint serves the web recipe editor when it exists.

## 6. Barcode → Product → Ingredient (deferred, task T11)

- The capture app scans a UPC and looks it up in `fdc_branded`, which returns brand, description, `branded_food_category` and package weight.
- It suggests an Ingredient by running the resolver on the branded description, with a category hint, and creates or links the Product.
- This fits the existing purchase-entry decision: receipt capture plus manual entry, with interpretation needed to link lines to products.
