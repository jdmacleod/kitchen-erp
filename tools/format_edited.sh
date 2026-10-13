#!/usr/bin/env bash
# Claude Code PostToolUse hook (.claude/settings.json): format the file an Edit,
# Write or MultiEdit just touched, so an agent never argues with the formatter.
# Reads the hook's JSON on stdin. Silent on success and always exits 0: a
# formatter that cannot run must never block an edit; pre-commit and CI still
# check everything. See CODING_STANDARDS.md, "The three layers".

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v jq >/dev/null 2>&1 || exit 0
file="$(jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -n "$file" ] && [ -f "$file" ] || exit 0

case "$file" in
    "$root"/*) rel="${file#"$root"/}" ;;
    /*) exit 0 ;;
    *) rel="$file" ;;
esac

# From the repository root, so ruff reads backend/pyproject.toml for backend/ and
# tools/ruff.toml (which extends it) for tools/, exactly as the pre-commit hooks do.
cd "$root" || exit 0

case "$rel" in
    backend/*.py | tools/*.py)
        uv run --quiet --project backend ruff format --quiet "$rel" >/dev/null 2>&1
        uv run --quiet --project backend ruff check --quiet --fix --select I "$rel" >/dev/null 2>&1
        ;;
    frontend/*.ts | frontend/*.tsx | frontend/*.css | frontend/*.json | \
        e2e/*.ts | e2e/*.json)
        # The Prettier pinned in frontend/ (e2e/ pins the same version), with the
        # root .prettierrc.json and .prettierignore, as the pre-commit hook runs it.
        corepack pnpm --dir frontend exec prettier --write --log-level silent \
            --ignore-path ../.prettierignore "$root/$rel" >/dev/null 2>&1
        ;;
esac

exit 0
