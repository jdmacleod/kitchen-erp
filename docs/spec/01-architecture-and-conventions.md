# 01 — Architecture and Conventions

## Shape of the system

Kitchen ERP is a conventional three-tier web application with one unconventional property: nearly all of its difficulty is in data resolution rather than in serving requests. Cryptic receipt text must become products, free-text quantities must become canonical amounts, and both must tolerate being wrong or incomplete. The architecture therefore favours a plain, boring request path and invests its structure in two places: a pure conversion library and a staged ingest pipeline.

One deployment serves one household. There is no multi-tenancy. Household members are users of the same dataset with two roles, `admin` and `member`; in Phases 1–2 the only difference is that admins manage users and API tokens.

## Services

The Compose file defines five services. `db` is PostgreSQL 16 using the `postgis/postgis` image, with the `postgis` and `pg_trgm` extensions enabled by the first migration. `api` is the FastAPI application. `worker` runs the same image as `api` with a different entrypoint and executes ingest stages. `web` serves the built React application and static map tiles from `data/tiles/`, and reverse-proxies `/api` to `api`, so the browser sees one origin. An optional bind mount of the household's Cooklang recipe repository at `data/recipes/`, configured by `RECIPES_PATH`, is declared from the start so that Phase 3 needs no Compose change; the recipes repository is never a submodule. `ollama` is defined under the `llm` Compose profile for Linux hosts with a usable GPU; on macOS the model server runs natively on the host and the application reaches it through `OLLAMA_BASE_URL`. A `routing` service for drive-time computation arrives in Phase 4 and is not part of this package.

There is no message broker. The worker claims jobs from the `ingest_job` table using `SELECT … FOR UPDATE SKIP LOCKED`, which is ample for a household's volume and removes a moving part. A job that stays locked longer than a configurable timeout is considered abandoned and becomes claimable again.

Persistent state lives in two places: the PostgreSQL volume and the `data/` directory, which holds receipt images, map tiles, and optional reference downloads. Receipt images are stored content-addressed at `data/receipts/<aa>/<bb>/<sha256>.<ext>`. Backing up the deployment means backing up those two things.

## Configuration

Configuration comes from environment variables, loaded through a Pydantic settings class and documented in `.env.example`. The notable ones are `DATABASE_URL` for the runtime role and `MIGRATION_DATABASE_URL` for the owner role; `OLLAMA_BASE_URL` and `LLM_MODEL` (default `gpt-oss:20b`, any model that honours JSON-schema-constrained output will do); `OCR_ADAPTER` (`tesseract` by default); `ENABLE_OVERPASS` and `ENABLE_NOMINATIM` (both false by default); `TILES_PATH`; `RECIPES_PATH`; `HOUSEHOLD_TIMEZONE` (default `America/Los_Angeles`), which is the zone used to interpret times printed on receipts and to evaluate opening hours; `CURRENCY` (default `USD`; the system is single-currency and stores no currency column); and `INGEST_LOCK_TIMEOUT_SECONDS`. The application must start and be fully usable for manual workflows with no model server reachable; ingest jobs that need the model wait and retry with backoff rather than failing the deployment.

## Database roles and append-only enforcement

Two roles exist. `kerp_owner` owns the schema and runs migrations. `kerp_app` is the runtime role used by `api` and `worker`. Migrations grant `kerp_app` full DML on ordinary tables and only `SELECT` and `INSERT` on the append-only tables: `price_observation`, `price_observation_void`, and `ingest_stage_result`. A `BEFORE UPDATE OR DELETE` trigger on each of those tables raises an exception as a second line of defence, so that even a misgranted role cannot rewrite history. Tests assert both mechanisms: that an update attempted as `kerp_app` fails on privilege, and that an update attempted as `kerp_owner` fails on the trigger.

Corrections follow from this. A wrong price is voided by inserting a `price_observation_void` row with a reason, and the right price is inserted as a new observation. Reads go through a view that excludes voided rows.

## Conventions

Tables are singular snake_case. Every mutable table carries `created_at` and `updated_at`; tables that record human input also carry `created_by`. Enumerated values are `text` columns with `CHECK` constraints rather than PostgreSQL enum types, because they are far easier to evolve in migrations. Names that must be unique for humans (`ingredient.name`, `vendor.name`) are unique case-insensitively through a unique index on `lower(name)`. Geography uses `geography(Point, 4326)`.

The API is versioned under `/api/v1`. Resources follow ordinary REST shapes with cursor pagination on list endpoints. Decimals cross the wire as strings. Errors use a single envelope with a stable machine-readable `code`, a human `message`, and optional `details`; typed conversion failures and ingest failures surface their type as the `code`. Browser sessions authenticate with an HTTP-only cookie backed by a `session` table, so that sessions can be revoked individually or all at once; programmatic clients, including the future capture app, authenticate with a bearer token whose hash is stored in `api_token`. Passwords are hashed with Argon2id. Mutating endpoints that a mobile client may retry accept an `Idempotency-Key` header. Keys are scoped to the authenticated user and stored in `idempotency_key` with a hash of the request and the response that was returned; a retry with the same key and the same request replays the stored response, a retry with the same key and a different request is rejected with a 422, and keys older than 24 hours are swept. `GET /api/v1/health` returns only an overall status without authentication; the per-check details require a session or token.

The frontend defaults to Tailwind with a headless component library; this is a default, not a decision. What is a decision is that the review and manual-entry screens must be fully keyboard-operable on desktop and comfortable one-handed on a phone, because those two screens determine whether the system gets used.

## Testing

The conversion library is tested with Hypothesis: round trips within a dimension return the original quantity to within a stated tolerance, conversions compose, and every failure path returns the right typed failure. Services are tested against the Compose `db` service, in a throwaway database that the harness creates and drops per run using the owner role, never against SQLite or mocks, because PostGIS, trigram search, and privilege enforcement are all part of the behaviour under test. Ingest is tested with a fixture corpus of synthetic receipts, described in the Phase 2 document, with the language model replaced by recorded responses so that the default suite is deterministic and offline. A separate opt-in suite marked `llm` runs the same corpus against a live model and reports accuracy rather than asserting exact output. Browser behaviour that the acceptance criteria assert on, such as network activity and phone-width layouts, is tested with Playwright against the Compose stack.

## Observability

Structured JSON logs with a request or job identifier on every line. Logs carry identifiers, stage names, durations, and error codes, never receipt text, OCR output, model output, or stage results: those hold whatever a receipt prints and belong only in the database and its backups. Error messages that would otherwise echo raw text redact it. Each ingest stage records its adapter, adapter version, duration, and output in `ingest_stage_result`, which doubles as the audit trail for how any purchase line came to be what it is. A `/api/v1/health` endpoint reports database connectivity, migration head, model-server reachability, and queue depth.
