# Keeping recipe names in the vocabulary

The recipes a deployment reads are Cooklang files in a repository the
household owns. Kitchen ERP never writes to that repository: it indexes the
files, resolves each ingredient name against the catalog's names and
spellings, and queues what it cannot resolve for a person. Three `kerp
recipes` commands help keep the names in the files close to the catalog
(`docs/spec/07`, the 1G amendments).

All three need the database, because the names and spellings live there, so
they run where `kerp` runs: `docker compose exec api kerp recipes …`, or
`uv run kerp recipes …` with `DATABASE_URL` set. The directory they read
defaults to the configured recipes mount for the two read-only commands;
`conform` always takes an explicit `--path`, because it writes.

## `kerp recipes vocabulary`

Lists every distinct ingredient name across the files, with how often it is
used and in which files, in one of these buckets:

| bucket        | meaning                                                                                   |
| ------------- | ----------------------------------------------------------------------------------------- |
| `conformant`  | exactly an ingredient's name or one of its spellings                                      |
| `inflection`  | a plural or other inflection of one (`eggs` for egg); the application resolves it as a hit |
| `drift`       | not a hit, but a confident target: an exact standard-list entry, or the name without its prep words (`minced garlic` → garlic, note "minced") |
| `unknown`     | nothing the catalog or the standard list knows                                            |
| `negligible`, `ignored` | names the application never counts (`salt`, `pepper`, `water`; names marked "not an ingredient") |

Trigram similarity never counts as drift: a close match is a suggestion in the
inbox, never something these commands act on.

The table goes to stdout and the full report to a file: `--out report.json`
or `--out report.csv`, or by default a dated JSON file under
`data/vocabulary/` (`VOCABULARY_REPORTS_PATH`). Reports never land in the
recipes repository.

## `kerp recipes lint [--strict]`

Prints the `drift` and `unknown` names with the suggested target where there
is one, and the files that do not parse. The exit code is 0 unless `--strict`
is given and any such name or parse error exists; then it is 1.

### An optional pre-commit hook for a recipes repository

This is documented, not installed: a recipes repository stays a plain
collection of text files unless its owner wants the check. In the recipes
repository, `.git/hooks/pre-commit`:

```sh
#!/bin/sh
# Refuse a commit while a recipe names an ingredient the catalog does not know.
# Needs the Kitchen ERP stack running; when it is down the check is skipped
# with a note, so run `kerp recipes lint` once it is back.
compose="docker compose -f $KITCHEN_ERP_DIR/compose.yaml"
if [ -z "$($compose ps -q api 2>/dev/null)" ]; then
  echo "kitchen-erp is not running: recipe names were not checked" >&2
  exit 0
fi
$compose exec -T api kerp recipes lint --strict --path /data/recipes
```

`KITCHEN_ERP_DIR` is wherever the Kitchen ERP checkout lives; the mount
`/data/recipes` is this repository as the stack sees it. Make the hook
executable (`chmod +x .git/hooks/pre-commit`). Hooks are per clone and are
not committed with the repository.

## `kerp recipes conform --path DIR [--apply]`

Rewrites drift names to their target, in place. Without `--apply` it prints
the planned changes as a unified diff and writes nothing. With `--apply` it
writes the files and stops: it never commits, so the next step is reading
`git diff` in the recipes repository and committing it yourself.

What a rewrite does, and only this:

- The name inside the `@…` token becomes the target's name, in the token's
  own case (`@minced garlic` → `@garlic`, `@Minced garlic` → `@Garlic`).
- Stripped prep words move into the note: `(minced)` is added, or put in
  front of an existing note, `(minced; for the sauce)`.
- `{}` is added when a token without braces gets a multi-word name or a note,
  because `@balsamic vinegar` with no braces would read as the single word
  `balsamic`.
- Every byte outside the rewritten tokens stays as it was: quantities, units,
  comments, front matter, line endings.

Files with no drift are not touched; files that do not parse are reported
and skipped; a file whose rewrite would not read back exactly as intended is
skipped and named. A second run reports no drift and changes nothing. Names
that drift to a standard-list entry the catalog does not have yet are
rewritten to the entry's name and then show as `unknown` with that hint until
someone creates the ingredient from the inbox.
