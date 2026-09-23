.DEFAULT_GOAL := help
PY ?= python3

help:  ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

setup:  ## Install git hooks and create the local data directories
	pre-commit install
	@mkdir -p data/receipts data/tiles data/usda data/imports
	@echo
	@echo "Now build tools/denylist.txt (gitignored) — see SECURITY.md."
	@echo "It also writes tools/denylist.salt and refreshes tools/denylist.hashes."

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

e2e-seed:  ## Create the local dev admin used by the browser tests (idempotent)
	@docker compose exec -T api kerp create-admin --email admin@example.com --display-name Admin --password local-dev-admin-pw >/dev/null 2>&1 || true
	@echo "dev admin present"

e2e:  ## Run the Playwright suite against the Compose stack (needs `docker compose up -d`)
	cd e2e && corepack pnpm install --frozen-lockfile && corepack pnpm exec playwright install chromium && corepack pnpm test

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
