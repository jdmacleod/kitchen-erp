.DEFAULT_GOAL := help
PY ?= python3

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

setup:  ## Install git hooks and create the local data directories
	pre-commit install
	@mkdir -p data/receipts data/tiles data/usda data/imports
	@echo
	@echo "Now build tools/denylist.txt (gitignored) — see SECURITY.md."

check-pii:  ## Scan the working tree for personal data
	$(PY) -m tools.scan_pii

check-pii-history:  ## Scan commit messages and diffs across all history
	$(PY) -m tools.scan_pii --history

denylist:  ## Harvest denylist entries from a real receipt's text: make denylist FILE=path
	$(PY) -m tools.build_denylist $(FILE)

check:  ## Everything CI runs for safeguards
	$(PY) -m tools.scan_pii
	pre-commit run --all-files
