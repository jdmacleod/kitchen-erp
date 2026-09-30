# 13 — Ingredient Data Model & Lexicon Format

**Status: draft for review, 2026-09-30. Not approved for implementation; `CLAUDE.md` names Phases 1 and 2 as the approved scope.** Documents 12–15 arrived as a handoff from a design session and are kept close to how they arrived; their tensions with documents 00–11 are being settled in planning.

Adapt the names and types to the existing kitchen-erp schema, migrations tooling and ER diagram. The DDL below is illustrative Postgres. Where it conflicts with existing Ingredient or Product tables, **extend the existing tables rather than duplicating them**, and flag any conflict.

## 1. Source of truth

- **Recipes** are Cooklang files in the adjacent `cooklang-recipes` repo. This is already decided. The indexer reads the working tree and sets a "dirty" marker for uncommitted changes.
- **The ingredient lexicon** (canonical names, parents, aliases, preferred references) lives **in the recipes repo** as `lexicon/ingredients.yaml`. The recipe collection then stays self-describing without the ERP running, and lexicon changes are reviewed in the same git history as the recipe rewrites they cause.
- **The DB mirrors the lexicon.** The indexer and sync load YAML into the tables. Edits made in the ERP UI (inbox resolutions) write back to the YAML in the working tree, which shows as dirty until committed, following the same model as recipes.
- **FDC data is reference data in staging tables.** It is re-importable, never hand-edited, and not in git.

## 2. Tables

```sql
-- Canonical vocabulary (mirrors lexicon/ingredients.yaml)
create table ingredient (
  id              bigserial primary key,
  slug            text not null unique,          -- kebab-case, stable forever (renames change name, not slug)
  name            text not null unique,          -- canonical display name, natural recipe form
  plural          text,                          -- optional explicit plural/inflection for rendering
  parent_id       bigint references ingredient(id),
  is_abstract     boolean not null default false,-- grouping node (e.g. 'cheese'); recipes may still reference it
  category        text,                          -- first-cut: FDC food_category description; revisit later
  density_g_per_ml numeric,                      -- derived from preferred FDC portions; nullable
  status          text not null default 'active',-- active | proposed | deprecated
  merged_into_id  bigint references ingredient(id), -- set when deprecated by merge; resolver follows it
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table ingredient_alias (
  id            bigserial primary key,
  ingredient_id bigint not null references ingredient(id) on delete cascade,
  alias         text not null,
  alias_norm    text not null unique,            -- normalized key (see §4); unique across ALL ingredients
  kind          text not null,                   -- synonym | inflection | misspelling | legacy | common_name
  source        text not null                    -- curated | fdc_common_name | foodon | off | llm_accepted | conform
);

create table ingredient_ref (
  id            bigserial primary key,
  ingredient_id bigint not null references ingredient(id) on delete cascade,
  system        text not null,                   -- fdc | foodon | off | wikidata
  external_id   text not null,                   -- '170005', 'FOODON_03311340'
  is_preferred  boolean not null default false,  -- at most one preferred per (ingredient, system)
  note          text,
  unique (system, external_id, ingredient_id)
);
create unique index one_preferred_ref on ingredient_ref(ingredient_id, system) where is_preferred;
```

The existing **Product** table gets (or already has) `ingredient_id` and an optional `gtin_upc`. The Product→Ingredient link is how a DeCecco box satisfies `@pasta`.

### FDC staging tables (reference data)

Keep these slim. `food_nutrient.csv` (1.8 GB) is out of scope for this effort. If nutrition is needed later, import it filtered to referenced `fdc_id`s only.

```sql
fdc_food(fdc_id pk, data_type, description, category, ndb_number, fndds_uses int)
  -- only foundation_food + sr_legacy_food + survey_fndds_food; plus pseudo-rows for FNDDS-only ingredient codes (fdc_id null, fndds_code)
fdc_attribute(fdc_id, kind, value)       -- kind in: foodon_id, foodon_name, common_name, scientific_name, ncbi_taxon
fdc_portion(fdc_id, amount, unit_raw, unit_norm, modifier, gram_weight)   -- unit_norm filled by parser (doc 14 §2)
fdc_branded(gtin_upc pk, fdc_id, brand_owner, brand_name, description, branded_food_category, package_weight)
  -- slim; ingredients text excluded; optional/deferred (task T11)
```

## 3. Inflections are not drift

- Canonical `egg` + recipe text `@eggs{3}` counts as **conformant**. The conform pass registers `eggs` as an `inflection` alias and leaves the text alone.
- Drift means a *different* name for the same identity: `@green onion` vs `scallion`, `@parmigiano` vs `parmesan`, `@minced garlic`.
- Only drift gets rewritten.

## 4. Normalization key (`alias_norm`)

Use one function, shared by the resolver, the sync and the lint, so behaviour stays deterministic:

1. Unicode NFKC. Strip accents for the *key only* (jalapeño → jalapeno); keep them in display names.
2. Lowercase and trim. Collapse whitespace. Remove punctuation except hyphens inside words.
3. Do **not** singularize in the key. Plurals are explicit `inflection` aliases, which avoids "gras" / "grass"-style stemming errors. The sync auto-generates regular inflections (`+s`, `+es`, `y→ies`) as aliases, and curated overrides win.

`alias_norm` is globally unique. If two ingredients claim the same alias, it is a data error surfaced in the inbox, not a silent tie.

## 5. Lexicon file format (`cooklang-recipes/lexicon/ingredients.yaml`)

```yaml
version: 1
ingredients:
  - slug: scallion
    name: scallion
    aliases: [green onion, spring onion]          # inflections auto-generated; list only real synonyms
    refs: { fdc: 170005, foodon: FOODON_03311340 }
  - slug: cheese
    name: cheese
    abstract: true
  - slug: parmesan
    name: parmesan
    parent: cheese
    refs: { fdc: 171247 }
  - slug: parmigiano-reggiano
    name: Parmigiano-Reggiano
    parent: parmesan
    aliases: [parmigiano]
  - slug: olive-oil
    name: olive oil
    parent: cooking-oil
    refs: {}                                       # empty refs allowed; shows in "needs reference" inbox view
    status: proposed
```

The rules:
- **Slugs are immutable.** A rename changes `name` and moves the old name into `aliases` with `kind: legacy`.
- **Merges** set `status: deprecated` and `merged_into: <slug>` on the loser.
- **Ordering:** sorted by slug. This keeps diffs clean.
- **Only `fdc` and `foodon` are needed in `refs` initially.** Collapsed/secondary FDC IDs stay in the DB (`ingredient_ref` with `is_preferred=false`) and don't need to be in YAML. Keep the YAML human-sized.

## 6. Cooklang specifics that affect rewriting

- A single-word ingredient can be `@salt`. A multi-word ingredient **requires braces**: `@olive oil{}` or `@olive oil{2%tbsp}`. A rewrite that changes word count must add `{}` if it isn't already there.
- The note is the parenthetical immediately after the ingredient: `@garlic{3%cloves}(minced)`. When moving prep words out of a name, create the note, or prepend to an existing note (`(minced, divided)`).
- Rewrites touch only the ingredient token span. Never reflow lines or change quantities, units, steps or metadata.
- Verify the exact grammar against the cloned `cooklang` reference repo and its parser before implementing the rewriter. Prefer a real parser that keeps source spans over regex.
