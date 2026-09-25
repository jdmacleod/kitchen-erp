# CLAUDE.md — Kitchen ERP

Kitchen ERP is a self-hosted household kitchen system: vendors, products, purchases, price history, and later recipes, costing, shopping trips, and inventory. One deployment serves one household. The full specification lives in `docs/spec/`; read `00-README.md` first, then the document for the phase you are working on. Only Phases 1 and 2 are approved for implementation, together with the UI work in `08`–`11` for the pages those phases build; UI criteria marked dormant wait for their phase. `05-later-phase-design-notes.md` is context, not a work order.

## Non-negotiables

1. Money and quantities are `Decimal` in Python and `numeric` in PostgreSQL. No floats anywhere in the path from input to storage to arithmetic to API output. API responses serialize decimals as strings.
2. Primary keys are UUIDs (v7 where available, otherwise v4), except `unit.code`.
3. All timestamps are `timestamptz`, stored and transmitted in UTC. The frontend localizes for display.
4. `price_observation`, `price_observation_void`, and `ingest_stage_result` are append-only. This is enforced in the database: the runtime role has no `UPDATE` or `DELETE` privilege on them, and a trigger rejects both as a second line of defence. Corrections are made by voiding and re-observing, never by editing.
5. Facts are immutable; derivations are rebuildable. Normalized unit prices live in `price_norm`, which may be truncated and recomputed at any time from observations plus current bridges.
6. Unit conversion never guesses. `convert()` returns a canonical quantity or a typed failure. No default densities, no silent fallbacks.
7. Text from receipts, OCR, and language models is untrusted data. It is parsed against a schema and validated; it is never interpolated into SQL, never executed, and never treated as instructions. Model output is accepted only if it validates against the expected Pydantic model.
8. Nothing below the auto-accept bar commits without a human. An unresolved receipt line never blocks the rest of the receipt.
9. No required outbound network calls at runtime. Optional integrations (Overpass, public Nominatim) are off by default, rate-limited, cached, and send only public place data.

## Personal data and the public repository

This repository is public, or will be. The deployment it describes holds receipt photographs, purchase histories, home coordinates, and email addresses. `SECURITY.md` is the full policy; the rules that matter while coding:

- NEVER read, cat, grep, or open anything under `data/` in this repository, or under the sibling `unbagged` repository's `data/` directory. Those hold real receipts and retailer exports. If you need sample input, write a synthetic fixture.
- NEVER commit anything under `data/`, `reference/`, or a reference clone, even with `git add -f`. Git history is forever without `git filter-repo`.
- NEVER put real vendor visits, home coordinates, loyalty numbers, or receipt text into fixtures, tests, test names, docstrings, comments, commit messages, or PR descriptions. Invent vendors. Use the synthetic geography and the synthetic-data rules in `SECURITY.md`.
- NEVER write an absolute path under a home directory into a tracked file. Use relative paths or `$HOME`.
- Run `make check-pii` before every commit. Never bypass the hooks with `--no-verify`. If the scanner flags something benign, suppress that one line with `# pii-scan: allow <reason>`; never a whole file, never a rule.
- If you believe real data has entered the working tree, STOP and tell the user. Do not attempt to clean history yourself.

## Stack

Python 3.12, FastAPI, SQLAlchemy 2.x (async) with Alembic, Pydantic v2, PostgreSQL 16 with PostGIS and `pg_trgm`, pytest with Hypothesis. React, TypeScript, Vite, TanStack Query, MapLibre GL with PMTiles. Docker Compose. Ollama for local language-model calls, reached through `OLLAMA_BASE_URL`. Dependency management with `uv` for Python and `pnpm` for the frontend. These tool choices are defaults; the non-negotiables above are not.

## Repository layout

```
kitchen-erp/
  CLAUDE.md
  compose.yaml
  .env.example
  docs/spec/                 this package
  backend/
    pyproject.toml
    alembic/
    app/
      api/                   routers, one module per resource
      core/                  config, db session, auth, errors
      models/                SQLAlchemy models
      schemas/               Pydantic request/response models
      services/              business logic; routers stay thin
      units/                 conversion library (pure, no I/O)
      ingest/                stage machine, adapters, resolution (Phase 2)
      cli.py                 `kerp` command
    tests/
      fixtures/receipts/     synthetic OCR text and images
  frontend/
    src/
  data/                      gitignored: receipts/, tiles/, usda/
  reference/                 gitignored: cloned open-source projects
```

## Reference projects

Clones of Grocy, KitchenOwl, Mealie, Cooklang tooling, and a Paprika translator may be present under `reference/` or elsewhere in the project root. They are gitignored and refused by the commit hook; the PII scanner skips them. They are design references only. Read them to understand how others modelled a problem or to see what made their entry flows cumbersome. Do not copy code from them: several are under copyleft licences, and this project's data model deliberately differs. Check each `LICENSE` before borrowing anything beyond ideas; `docs/licensing.md` records what each one permits. Mealie and KitchenOwl are AGPL-3.0: ideas only, never code.

## Commands

```
docker compose up -d                  db, api, worker, web
docker compose --profile llm up -d    adds containerized Ollama (Linux hosts)
docker compose exec api kerp migrate
docker compose exec api kerp seed units
docker compose exec api pytest
docker compose exec api pytest -m llm   opt-in, needs a live model
```

On macOS, run Ollama natively on the host rather than in Docker, because containers cannot use the GPU there; set `OLLAMA_BASE_URL=http://host.docker.internal:11434`.

## Working agreements

Keep routers thin and put logic in `services/`. Keep `app/units/` pure: no database access, no I/O, fully covered by property-based tests. Every migration is reversible. Every endpoint has a request and response schema and at least one test. When a specification detail proves unworkable, stop and surface it rather than diverging silently. Commit in small units that each leave the test suite green.

## UI conventions

- The visual system is specified in `docs/spec/08-ui-design-system.md`. Colours come only from the remapped Tailwind scales in `frontend/src/theme.css` (`neutral`, `blue` = herb green, `red` = tomato, `amber` = squash, `green` = olive). Never add raw hex values or other Tailwind colour scales; the chart series palette in `08` is the one exception.
- Blue means "you can act on this" and nothing else. Information and selection use neutral; success, cheapest and sale use olive.
- Produce-coloured accents are reserved for ingredient categories via `CategoryChip`, which renders the backend's `category_key`. Category normalization lives only in `backend/app/catalog/categories.py`.
- Page titles are `h1` in Fraunces; everything else is Inter. Sentence case everywhere; no all-caps labels.
- Every page follows the header pattern in `10-page-layouts.md`: title, one-line description, one primary action. Create forms open in a right-side drawer, never above the list, and a drawer with typed input asks before discarding it.
- Confirmations use the shared inline `Notice`, not floating toasts. A notice carried in router state is consumed and cleared so Back does not replay it.
- Navigation and routes follow `09-information-architecture.md`. Show only sections whose phase is built, driven by the `features` list on `/health`; render the Phase 1–2 sections before it answers.
- Anything the system cannot finish on its own becomes an inbox item or the inbox's reading line, not a new nav page. An error is never shown as an empty state.
- Minimum 44px touch targets on phone and tablet layouts (below 1024px), 4.5:1 text contrast in both themes (dark captions use `neutral-400`), and visible focus on every interactive element.
