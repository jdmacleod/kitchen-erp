# 15 — Ingredient Vocabulary: Task Plan

**Status: draft for review, 2026-09-30. Not approved for implementation; `CLAUDE.md` names Phases 1 and 2 as the approved scope.** Documents 12–15 arrived as a handoff from a design session and are kept close to how they arrived; their tensions with documents 00–11 are being settled in planning.

**First session for Claude Code:**
- Read docs 12–14.
- Inspect the existing kitchen-erp schema, migrations, unit/conversion layer, indexer and inbox model.
- Write a short **reconciliation note** listing where this spec conflicts with or duplicates existing code, and propose adjustments.
- Wait for the household's approval before T3.

Tasks are ordered, and parallel where noted. Each has acceptance criteria (AC).

### T1 — FDC staging import
Port `tools/fdc_profile.py` logic into the real importer (doc 14 §1). Place it with the repo's other tooling.
- **AC:** Loads the 2026-04-30 zip in one transaction. Row counts match doc 14 §1. Re-running is idempotent.
- **AC:** Includes a FoodOn ID/name, common name and scientific name per core food, and an FNDDS usage count. FNDDS-only codes are stored as pseudo-rows.
- **AC:** The branded load is behind `--with-branded` and skips the `ingredients` column.

### T2 — Portion parser & density (parallel with T3 after T1)
- **AC:** At least 95% of core-food portion rows get a `unit_norm`. Report the top unparsed modifiers.
- **AC:** Densities are computed for Tier A ingredients once references are set. Spot-check flour (about 125 g/cup), sugar (about 200 g/cup) and water (about 237 g/cup) against the existing conversions layer.

### T3 — Schema & migrations
Implement doc 13 §2 against the existing schema, extending existing Ingredient/Product rather than duplicating them.
- **AC:** Migrations apply and roll back cleanly.
- **AC:** The one-preferred-ref-per-system constraint holds.
- **AC:** `alias_norm` is globally unique, and the normalization function is shared and unit-tested.

### T4 — Lexicon file + sync
Implement `lexicon/ingredients.yaml` load (YAML → DB) and write-back (DB → YAML, sorted, stable formatting).
- **AC:** A round-trip of an unchanged file produces a byte-identical output.
- **AC:** Inflections are auto-generated, with curated overrides respected.
- **AC:** A duplicate `alias_norm` across ingredients fails loudly and names both slugs.
- **AC:** Write-back leaves `cooklang-recipes` dirty and never commits.

### T5 — Seed Tier A
Convert `docs/spec/assets/tier-a-ingredients.csv` into the initial lexicon.
- Build a helper that, for each row whose `review_note` flags a cooked reference, lists raw or dry sibling candidates from `fdc_food` (same head noun, "raw"/"dry"/"uncooked" in the description). A person picks.
- Resolve the three rows with no reference: vegetable oil, olive oil, mushroom.
- Load USDA common names as `common_name` aliases, but **only** where they don't collide with another ingredient.
- **AC:** Every concrete ingredient has a preferred FDC reference, or is explicitly `status: proposed`.
- **AC:** No FoodOn names flagged wrong in the CSV notes are used as names.
- **AC:** The open identity questions (doc 12 §6) are listed as inbox or TODO items, not decided.

### T6 — Recipe vocabulary extraction (can run right after T4, before T5 finishes)
Extract only (conform stage 1 + report), with no rewrites.
- **AC:** Produces the distinct ingredient names across `cooklang-recipes`, with occurrence counts and files, bucketed against the current lexicon: conformant, inflection, unknown.
- **AC:** The report explicitly lists Tier A items **not** used in any recipe (candidates to drop) and recipe names not in Tier A (the gourmet gap).
- Share the report with the household before T8, because it may reshape the seed.

### T7 — Resolver service
Doc 12 §3 cascade, with the prep/form word lists as data files.
- **AC:** Tests with fixtures cover at least:
  - `minced garlic` → garlic + note "minced";
  - `ground cumin` → *not* cumin seed;
  - `green onions` → scallion;
  - `Parmigiano` → parmigiano-reggiano (or parmesan if the child doesn't exist);
  - `eggs` → egg (inflection);
  - `fresh basil` → basil;
  - `dried basil` → dried basil;
  - `unsalted butter` ≠ `salted butter`.
- **AC:** The Ollama tier returns schema-valid JSON or a clean failure. Failures never auto-apply.
- **AC:** Every resolution logs its method and confidence.

### T8 — Conform pass CLI
Doc 12 §4 with `--apply`.
- **AC:** A dry run produces the four-bucket report.
- **AC:** `--apply` rewrites only drift token spans. It adds `{}` for multi-word names and moves prep words into notes.
- **AC:** A golden-file test shows unchanged bytes outside the rewritten spans.
- **AC:** Never commits. A second run after applying reports zero drift.

### T9 — Inbox integration
Doc 12 §4 review items appear in the unified Home inbox, with actions map / alias / new / ignore.
- **AC:** Each action writes through to the DB and lexicon YAML.
- **AC:** Duplicate-alias and missing-reference issues also appear as inbox items.

### T10 — Authoring: suggest endpoint + lint
Doc 12 §5.
- **AC:** `suggest` returns canonical plus the matched alias in under 50 ms on the seeded lexicon.
- **AC:** `lint-recipes` has advisory and `--strict` modes.
- **AC:** An optional pre-commit hook snippet is documented for `cooklang-recipes`.

### T11 — Barcode lookup (deferred; do not start without the household's go-ahead)
Doc 12 §6.

## Out of scope for this package
- Nutrition display and `food_nutrient` import.
- Open Food Facts taxonomy import. It is an optional future synonym source; ODbL applies if seed data is published.
- Aisle taxonomy (a separate axis from ingredient hierarchy).
- A recipe editor UI.
