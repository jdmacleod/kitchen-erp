.DEFAULT_GOAL := help
PY ?= python3

# Build identity. Compose cannot run git, so make resolves it and exports it;
# every compose target below inherits it, including demo-up. A checkout with no
# git (a downloaded tarball) falls back to the same values the Dockerfile
# defaults to, so the UI says "dev" instead of inventing a version.
#
# BUILD_VERSION is `git describe`: the short sha today, a tag once tagging
# starts, and `-dirty` whenever the tree has uncommitted changes.
BUILD_COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
BUILD_VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
export BUILD_COMMIT
export BUILD_VERSION

help:  ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

setup:  ## Install git hooks and create the local data directories
	pre-commit install
	@mkdir -p data/receipts data/tiles data/usda data/imports data/routing
	@echo
	@echo "Now build tools/denylist.txt (gitignored) — see SECURITY.md."
	@echo "It also writes tools/denylist.salt and refreshes tools/denylist.hashes."

up:  ## Start the stack with build identity baked in, then migrate (use this, not bare compose)
	docker compose up -d --build
	@# The api does not migrate on start: until it does, health reports "failed" and pages
	@# that read new tables answer 500. `kerp migrate` is a no-op when already at head.
	@for i in $$(seq 1 30); do docker compose exec -T api kerp migrate && exit 0; sleep 2; done; echo "migrate never succeeded: see docker compose logs api" >&2; exit 1
	@echo "built $(BUILD_VERSION) ($(BUILD_COMMIT))"

down:  ## Stop the stack (and Ollama or OSRM, if started with their profiles); data is kept
	docker compose --profile llm --profile routing down

# The example recipes (examples/recipes, MIT) copied into the default mount so a
# fresh deployment has something to cost. Only the host writes to data/recipes:
# the containers mount it read-only. A `.git` there means a real repository,
# which this never touches. The worker's next scan (RECIPES_SCAN_SECONDS) picks
# the files up; `kerp recipes rescan` or POST /api/v1/recipes/rescan does it now.
seed-examples:  ## Copy the example recipes into data/recipes (never over a repository)
	@if [ -e data/recipes/.git ]; then \
		echo "data/recipes is a git repository; not touching it. Point RECIPES_PATH elsewhere or copy examples/recipes/ yourself." >&2; exit 1; fi
	@mkdir -p data/recipes
	@cp -R examples/recipes/. data/recipes/
	@echo "Copied $$(find examples/recipes -name '*.cook' | wc -l | tr -d ' ') example recipes into data/recipes/."
	@echo "The worker indexes them on its next scan; to index them now: docker compose exec api kerp recipes rescan"

# Drive times (17, 4B). The routing graph is built on the host from a regional
# OpenStreetMap extract (ODbL) clipped to ROUTING_BBOX, with osmium if it is
# installed and OSRM's own tools from the pinned image. Everything stays under
# data/routing/ (gitignored); the box is read from .env and never tracked.
OSRM_IMAGE := ghcr.io/project-osrm/osrm-backend:v5.27.1
ROUTING_BBOX ?= $(shell grep -E '^ROUTING_BBOX=' .env 2>/dev/null | cut -d= -f2-)
ROUTING_DIR := data/routing
OSRM_RUN := docker run --rm -t -v "$(CURDIR)/$(ROUTING_DIR):/data" $(OSRM_IMAGE)

routing-prepare:  ## Build the OSRM graph under data/routing/ from region.osm.pbf (clips to ROUTING_BBOX when osmium is installed)
	@mkdir -p $(ROUTING_DIR)
	@if [ ! -f $(ROUTING_DIR)/socal.osm.pbf ]; then \
		echo "$(ROUTING_DIR)/socal.osm.pbf is missing. Download the Geofabrik Southern California extract first:" >&2; \
		echo "  curl -L -o $(ROUTING_DIR)/socal.osm.pbf https://download.geofabrik.de/north-america/us/california/socal-latest.osm.pbf" >&2; \
		exit 1; fi
	@if [ -z "$(ROUTING_BBOX)" ]; then \
		echo "ROUTING_BBOX is unset: preparing the whole extract, which needs several GB of memory. Set it in .env (min_lon,min_lat,max_lon,max_lat) to clip."; \
		cp $(ROUTING_DIR)/socal.osm.pbf $(ROUTING_DIR)/region.osm.pbf; \
	elif command -v osmium >/dev/null 2>&1; then \
		echo "Clipping to ROUTING_BBOX with osmium…"; \
		osmium extract --overwrite -b "$(ROUTING_BBOX)" -o $(ROUTING_DIR)/region.osm.pbf $(ROUTING_DIR)/socal.osm.pbf; \
	else \
		echo "osmium is not installed (brew install osmium-tool / apt install osmium-tool): skipping the clip and preparing the whole extract."; \
		cp $(ROUTING_DIR)/socal.osm.pbf $(ROUTING_DIR)/region.osm.pbf; \
	fi
	$(OSRM_RUN) osrm-extract -p /opt/car.lua /data/region.osm.pbf
	$(OSRM_RUN) osrm-partition /data/region.osrm
	$(OSRM_RUN) osrm-customize /data/region.osrm
	@echo "Routing graph ready. Start it with: docker compose --profile routing up -d; then set ROUTING_URL=http://routing:5000 in .env and restart api and worker."

check-compose-env:  ## Every documented setting must reach a container
	$(PY) -m tools.check_compose_env

check-pii:  ## Scan the working tree for personal data
	$(PY) -m tools.scan_pii

check-denylist:  ## Verify the committed denylist digests and the salt agree
	$(PY) -m tools.denylist --self-test

check-pii-history:  ## Scan commit messages and diffs across all history
	$(PY) -m tools.scan_pii --history

denylist:  ## Harvest denylist entries from a real receipt's text: make denylist FILE=path
	$(PY) -m tools.build_denylist $(FILE)

# The browser tests run against their own throwaway stack (compose.e2e.yaml),
# never the household's: they create rows the app cannot delete.
E2E := docker compose -f compose.yaml -f compose.e2e.yaml
.PHONY: e2e e2e-up e2e-down  # e2e/ is also a directory, which made `make e2e` a no-op
E2E_WEB_PORT ?= 8082

e2e-up:  ## Start a fresh stack for the browser tests (web on :8082, its own volumes)
	$(E2E) down -v --remove-orphans
	$(E2E) up -d --build
	@# Migrate first: health reports "failed" until the schema exists.
	@for i in $$(seq 1 30); do $(E2E) exec -T api kerp migrate && exit 0; sleep 2; done; echo "e2e stack: migrate never succeeded" >&2; exit 1
	$(E2E) exec -T api kerp create-admin --email admin@example.com --display-name Admin --password local-dev-admin-pw
	@for i in $$(seq 1 60); do curl -sf http://127.0.0.1:$(E2E_WEB_PORT)/api/v1/health >/dev/null && exit 0; sleep 2; done; echo "e2e stack: not healthy after 2 minutes" >&2; exit 1

e2e-down:  ## Stop the browser-test stack and delete its volumes
	$(E2E) down -v --remove-orphans

e2e:  ## Run the Playwright suite on a throwaway stack, then delete it
	cd e2e && corepack pnpm install --frozen-lockfile && corepack pnpm exec playwright install chromium
	@# One shell, so the stack is deleted whether setup or the suite fails.
	$(MAKE) e2e-up && (cd e2e && E2E_BASE_URL=http://127.0.0.1:$(E2E_WEB_PORT) corepack pnpm test); status=$$?; $(MAKE) e2e-down; exit $$status

DEMO := docker compose -f compose.yaml -f compose.demo.yaml

demo-up:  ## Start the throwaway demo stack (web on :8081, its own volumes)
	$(DEMO) up -d --build
	$(DEMO) exec -T api kerp migrate

demo-seed:  ## Fill the demo stack with the synthetic household
	$(DEMO) exec -T api kerp seed demo

demo-down:  ## Stop the demo stack and delete its volumes
	$(DEMO) down -v

screenshots:  ## Recapture docs/screenshots from the demo stack (never from real data)
	cd e2e && corepack pnpm install --frozen-lockfile && corepack pnpm exec playwright install chromium
	cd e2e && corepack pnpm exec playwright test -c playwright.screenshots.config.ts

# Code standards (CODING_STANDARDS.md). Run from the repository root, so ruff
# reads backend/pyproject.toml for backend/ and its defaults for tools/, exactly
# as the pre-commit hooks do.
RUFF := uv run --project backend ruff

fmt:  ## Format the code the formatters own (Python today)
	$(RUFF) format backend tools
	$(RUFF) check --fix --select I backend tools
	@# The frontend half (Prettier over frontend/ and e2e/) arrives with the formatting PR.

lint:  ## Lint the backend, tools and frontend
	$(RUFF) check backend tools
	corepack pnpm --dir frontend lint

types:  ## Type-check the backend with ty (advisory: reports, never fails)
	cd backend && uv run ty check || true

size:  ## Fail files past the size limits; warn past the warning size
	$(PY) -m tools.check_size

size-report:  ## List every file over the preferred size, biggest first
	$(PY) -m tools.check_size --report

size-update:  ## Lower the size baseline after a baselined file shrinks
	$(PY) -m tools.check_size --update

check:  ## Everything CI runs for safeguards, plus the size check
	$(PY) -m tools.check_compose_env
	$(PY) -m tools.denylist --self-test
	$(PY) -m tools.scan_pii
	$(PY) -m tools.check_size
	pre-commit run --all-files
