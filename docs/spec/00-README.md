# Kitchen ERP — Handoff Package, Phases 1–2

This package specifies the first two phases of Kitchen ERP, a self-hosted household kitchen system covering vendors, products, purchases, price history, inventory, recipes, recipe costing, and location-aware shopping trips. It is written to be handed to Claude Code working in this repository's checkout. Phases 1 and 2 are specified to implementation depth. Phases 3–6 are described only as design notes so that early decisions do not paint later phases into a corner; they are explicitly not yet for implementation.

## How to use this package

Copy the files into `docs/spec/` in the repository and copy `CLAUDE.md` to the repository root. Start Claude Code with Phase 1. Each phase document is organized into sub-phases that can be built and merged independently, and each ends with numbered acceptance criteria. A sub-phase is done when every criterion in its block passes, the test suite is green, and `docker compose up` yields a working system from a clean checkout. Do not begin Phase 2 until all Phase 1 criteria pass, because Phase 2 leans on the conversion library and the vendor model.

Where this package records a decision, treat it as settled unless implementation proves it unworkable; in that case stop and raise it rather than quietly diverging. Where it says "default", the choice is a reasonable starting point that can be swapped without consulting anyone.

## File map

| File | Purpose |
|---|---|
| `00-README.md` | This overview, roadmap, glossary |
| `CLAUDE.md` | Repository-root instructions for Claude Code: non-negotiables, layout, commands |
| `01-architecture-and-conventions.md` | Stack, Compose services, database roles, testing and API conventions |
| `02-data-model.md` | Tables, constraints, and views for Phases 1–2, with forward-compatibility notes |
| `03-phase-1-reference-data.md` | Units and conversion, ingredients, products, vendors, places, map |
| `04-phase-2-purchases-and-price-book.md` | Receipt ingest, resolution, review, manual entry, price book, capture API |
| `05-later-phase-design-notes.md` | Decisions already made for Phases 3–6; context only |
| `06-open-questions.md` | Tensions found in the 2026-09-21 review; every recommendation was accepted and folded into the documents above |
| `07-phase-3-recipes-and-costing.md` | Phase 3 specified to implementation depth; a draft awaiting approval |
| `08-ui-design-system.md` | UI palette, category accents, typography, shape, core components, accessibility floor, map styling |
| `09-information-architecture.md` | Workflow-based navigation, routes and redirects, search, capture, the unified inbox, phone and tablet tabs |
| `10-page-layouts.md` | Approved layout of each desktop and phone page, with their states |
| `11-ui-acceptance-criteria.md` | UI sub-phases UI-1 to UI-4 with numbered acceptance criteria; approved alongside Phases 1–2, with later-phase criteria marked dormant |

## Roadmap

Phase 1 builds reference data: units and the conversion library, ingredients, products, vendors, locations, home bases, and the map. Phase 2 builds purchases and the price book: receipt ingest with learned per-vendor aliases, manual entry for markets and stands, shelf-price spotting, append-only price observations, a bulk importer for retailer purchase exports, and vendor comparison. Phase 3 adds recipes, indexed from the working tree of an adjacent `cooklang-recipes` git repository with uncommitted changes marked as such, and recipe costing. Phase 4 adds shopping lists, family sharing, and the trip planner. Phase 5 adds tiered inventory across both home bases. Phase 6 adds the native iOS capture app, built against the capture API that Phase 2 defines.

Purchases come before inventory deliberately. Purchases feed every downstream feature at the lowest data-entry cost, while inventory is the feature most likely to be abandoned if it is cumbersome. Recipe costing runs from the price book (standard cost), so it never depends on inventory being accurate.

## Guiding principles

Ease of use outranks completeness. Every workflow must tolerate partial information: a receipt commits with unidentified lines, a recipe costs with unpriced ingredients and says so, a conversion that lacks a density fails visibly rather than guessing. Human effort is spent once and remembered: confirming what a cryptic receipt line means writes an alias so the same line resolves automatically next time. Facts are immutable and derivations are rebuildable: what was paid, where, and when is append-only, while normalized unit prices are derived data that can be recomputed when a density or measure improves. Everything runs locally: the database, the language model, the map tiles. The only outbound calls are optional, low-volume, and carry no household data.

## Glossary

An **ingredient** is what a recipe asks for, such as dry rigatoni. A **product** is a specific buyable thing that fulfils an ingredient, such as De Cecco rigatoni in a 1 lb box, or the heirloom tomatoes from one particular stand. An **offer** is a product at a vendor location with an observed price. A **vendor** is a chain, independent shop, market, or stand; a **vendor location** is one physical site, and a market stall is a location whose parent is the market. A **home base** is a household site (there are two, about an hour apart) that anchors nearby vendor locations, and later stock locations and shopping lists. A **place** is a geographic point shared by home bases and vendor locations so that routing can treat them uniformly. The **canonical unit** of an ingredient is g, ml, or each; all quantities for that ingredient normalize to it. A **bridge** is ingredient-specific knowledge that crosses dimensions: a density (g per ml) or a **named measure** (a clove of garlic is about 5 g). A **price observation** is an immutable record that a quantity of a product cost a given amount at a location at a time. A **receipt alias** is a learned mapping from a vendor's normalized receipt text to a product, or to "ignore" for non-kitchen items.
