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
	@mkdir -p data/receipts data/tiles data/usda data/imports
	@echo
	@echo "Now build tools/denylist.txt (gitignored) — see SECURITY.md."
	@echo "It also writes tools/denylist.salt and refreshes tools/denylist.hashes."

up:  ## Start the stack with build identity baked in, then migrate (use this, not bare compose)
	docker compose up -d --build
	@# The api does not migrate on start: until it does, health reports "failed" and pages
	@# that read new tables answer 500. `kerp migrate` is a no-op when already at head.
	@for i in $$(seq 1 30); do docker compose exec -T api kerp migrate && exit 0; sleep 2; done; echo "migrate never succeeded: see docker compose logs api" >&2; exit 1
	@echo "built $(BUILD_VERSION) ($(BUILD_COMMIT))"

down:  ## Stop the stack (and Ollama, if started with the llm profile); data is kept
	docker compose --profile llm down

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

check:  ## Everything CI runs for safeguards
	$(PY) -m tools.check_compose_env
	$(PY) -m tools.denylist --self-test
	$(PY) -m tools.scan_pii
	pre-commit run --all-files
