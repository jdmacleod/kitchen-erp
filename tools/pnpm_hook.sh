#!/usr/bin/env bash
# Run a tool from frontend/'s or e2e/'s pinned node_modules on files pre-commit
# passes (CODING_STANDARDS.md, the three layers).
#
#   tools/pnpm_hook.sh <frontend|e2e> <tool> [args...] -- <file>...
#
# pre-commit passes paths relative to the repository root, but pnpm runs the tool
# from the package directory, where ESLint finds its config and suppressions; so
# the paths are made absolute first.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
package="$1"
shift

args=()
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    args+=("$1")
    shift
done
[ "$#" -gt 0 ] && shift

files=()
for file in "$@"; do
    files+=("$root/$file")
done
[ "${#files[@]}" -gt 0 ] || exit 0

exec corepack pnpm --dir "$root/$package" exec "${args[@]}" "${files[@]}"
