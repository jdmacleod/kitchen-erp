# Coding standards

How code in this repository is formatted, linted, sized and reviewed, and which
tool checks each rule. Most of the code is written by agents working in parallel
branches, so the rules live in tools wherever possible and this file explains
them. A rule no tool checks is marked as a guideline.

Existing code that breaks a rule is listed in a baseline (`# noqa` comments,
`eslint-suppressions.json`, `tools/size_baseline.json`) that may only shrink; new
code meets the rules. The baselines were taken by the formatting PR (#336) on
2026-10-13, which also reformatted the frontend with Prettier.

## 1. Purpose and precedence

- Tools enforce; this file explains. When the two disagree, the tool's
  configuration is the truth and this file is the bug.
- The non-negotiables in `CLAUDE.md` win over anything here: `Decimal` money, UUID
  keys, UTC timestamps, append-only facts, untrusted model text.
- `DESIGN.md` owns visual rules (colours, type, components); `SECURITY.md` owns
  personal data and fixtures. This file does not repeat them.
- A pull request that changes a tool's configuration updates this file in the
  same pull request.

## 2. The three layers

Fastest first. Each catches what the one before it cannot.

| Layer | When | What runs | Configured in |
|---|---|---|---|
| Format hook | After every Claude Code edit | `ruff format` and import sorting on the edited Python file; Prettier on the edited frontend or e2e file | `.claude/settings.json`, `tools/format_edited.sh` |
| Pre-commit | Every commit, staged files | Hygiene, gitleaks, ruff, Prettier, ESLint, the size check, actionlint, zizmor, markdownlint, shellcheck, hadolint, the PII scanner | `.pre-commit-config.yaml` |
| CI | Every push and pull request | Full lint and format checks, type checks, tests, size, types (advisory), repo hygiene, e2e, PII safeguards, CodeQL | `.github/workflows/` |

The format hook reads the edited path from the hook's JSON, formats `backend/` and
`tools/` Python from the repository root (so the result matches pre-commit), stays
silent, and always exits 0: a formatter that cannot run never blocks an edit.
Frontend and e2e TypeScript, CSS and JSON files get Prettier the same way, from
the version pinned in `frontend/`.

Prettier and ESLint run in pre-commit from the packages' own `node_modules`
(through `tools/pnpm_hook.sh`), so run `corepack pnpm install` in `frontend/` and
`e2e/` once before committing there. The ESLint hook lints only the changed
files, with the package's suppressions; when a fix makes a suppression unused, it
fails until you run `corepack pnpm exec eslint --prune-suppressions` in that
package.

The commands, from the repository root:

| Command | Does |
|---|---|
| `make fmt` | Format: ruff over `backend/` and `tools/`; Prettier over `frontend/` and `e2e/` |
| `make lint` | ruff check and format check over `backend/` and `tools/`; Prettier check, ESLint and `tsc` over `frontend/` and `e2e/` |
| `make types` | ty over the backend; reports, never fails |
| `make size` | The size check; `make size-report` lists files over the preferred size; `make size-update` lowers the baseline |
| `make check` | The safeguards, the size check and every pre-commit hook over all files |
| `pre-commit run --files <changed>` | The commit hooks on just what you changed |

Before finishing a change, run `make fmt lint` or `pre-commit run --files` on the
changed files. Never hand-format against the formatter: if its output looks wrong,
restructure the code, or raise it in the pull request.

## 3. Python

- **Formatting:** ruff format, line length 100, double quotes
  (`backend/pyproject.toml`, `[tool.ruff]`). `tools/` uses the same configuration
  through `tools/ruff.toml`, which extends it.
- **Lint:** ruff check with `E, F, W, I, B, UP, C4, SIM, PLW, PLE, PLR0912,
  PLR0915, C90, RUF, ASYNC, PT, S, TID, PTH, ERA`. Narrowed where a rule would
  only have produced noise:
  - `PT018` (one condition per assert) is off: about 930 test asserts combine
    conditions, and splitting them buys nothing.
  - In `backend/tests/`: `S101` (assert) and `S608` (SQL built from strings; the
    tests compose fixed fragments) are allowed. `S608` is also allowed in
    `alembic/versions/`, whose DDL and backfills are built from constants.
  - `ASYNC2xx` (blocking file and process calls in async functions) is allowed in
    tests, `app/cli.py` and `scripts/`: it matters on the server's event loop,
    not in one-shot commands. It would have added about 40 noqa comments.
  - `RUF001`–`RUF003` accept the typographic characters receipts and recipes use
    (`×`, `–`, `−`, curly quotes, `ʼ`, `´`, `⁄`; `allowed-confusables`).
- **Baseline:** the findings that predate these rules carry `# noqa: <code>`
  comments, added to 179 lines on 2026-10-13 (173 in `backend/`, 6 in `tools/`; mostly
  `C901`/`PLR0912`/`PLR0915` complexity, `S101` asserts in app code, `RUF012`,
  `RUF059`, `PT011` and `S603`). They may only be removed: fix the code, then
  delete the comment (ruff's `RUF100` flags one that no longer suppresses
  anything). Never add one to get a new change through.
- **Imports:** absolute, from `app.` (`from app.services.catalog import ...`), never
  relative; sorted by ruff's `I` rules.
- **Naming:** `snake_case` functions and modules, `PascalCase` classes,
  `UPPER_CASE` constants; a leading underscore for module-private helpers
  (guideline).
- **Numbers:** money and quantities are `Decimal`, never `float` (non-negotiable 1,
  enforced by the tests and review).
- **Output:** no `print` in `backend/app`. Logs go through
  `app.core.logging.get_logger`, which writes structured JSON with the request or
  job identifier; never log receipt text, OCR output or model output. The `kerp`
  CLI writes with `typer.echo` (guideline).
- **Structure:** routers stay thin, logic lives in `services/`, `app/units/` stays
  pure (from `CLAUDE.md`).

### Types: ty, advisory

The backend is checked with [ty](https://github.com/astral-sh/ty), pinned exactly
(`ty==0.0.86`) in the backend's dev dependencies and configured under `[tool.ty]`
in `backend/pyproject.toml`. It runs in `make types` and in the CI job
"Types (advisory)", which writes the count to the job summary. It gates nothing.

- **For ty:** it comes from Astral, like ruff and uv already used here; it checks
  the whole backend in a few seconds, fast enough for agent loops; good
  diagnostics and a language server; configured in `pyproject.toml`.
- **Against ty today:** it is beta (0.0.x), so rules and defaults change between
  releases and upgrades move the count; typing-spec conformance is incomplete
  (more false positives on advanced generics, overloads and descriptors); there is
  no Pydantic plugin (it relies on `dataclass_transform`) and no baseline file, so
  a gate would need per-rule or per-file suppressions.
- **Decision:** advisory. On 2026-10-13 it reports 486 diagnostics (484 errors,
  2 warnings; 268 of them in tests). Fix them as you touch the code; do not add
  `# ty: ignore` to make the number go down.
- **Review point:** make ty a gate once it publishes a stable release and the
  count on this repository is near zero. basedpyright and mypy are the mature
  fallbacks if it stalls. Upgrade ty in its own pull request and note the new count.

## 4. TypeScript and React

- **Formatting:** Prettier 3.9.10 (pinned exactly in both packages) with
  `printWidth` 100, double quotes and trailing commas everywhere, from one
  `.prettierrc.json` at the root shared by `frontend/` and `e2e/`;
  `.prettierignore` leaves out lockfiles, ESLint's suppressions, build and test
  output, screenshots and Markdown. `pnpm format` writes, `pnpm format:check`
  checks.
- **Lint (frontend):** ESLint flat config in `frontend/eslint.config.js`:
  `@eslint/js` recommended, typescript-eslint `strictTypeChecked` and
  `stylisticTypeChecked` with `parserOptions.projectService`, `react-hooks`,
  `jsx-a11y` recommended, `import-x/order` (alphabetized, no blank lines between
  groups), and `eslint-config-prettier` last. Two options relax the strict set to
  how React code is written here: `no-confusing-void-expression` allows arrow
  shorthand (`onClick={() => setOpen(false)}`), and
  `restrict-template-expressions` allows numbers. `pnpm lint` runs with
  `--max-warnings=0`; `pnpm typecheck` runs `tsc --noEmit`.
- **Lint (e2e):** `e2e/eslint.config.js`: typescript-eslint
  `recommendedTypeChecked` and `eslint-plugin-playwright` recommended (its
  warnings raised to errors), `eslint-config-prettier` last; `e2e/tsconfig.json`
  is strict. Scripts `lint`, `typecheck`, `format:check`.
- **Baseline:** findings that predate these rules are held in
  `frontend/eslint-suppressions.json` (595 findings in 163 files on 2026-10-13:
  mostly non-null assertions, function length, unnecessary conditions and
  complexity) and `e2e/eslint-suppressions.json` (54 in 9 files: conditional
  expects, conditional skips, `networkidle`). They may only shrink: after fixing
  one, run `corepack pnpm exec eslint --prune-suppressions` in that package.
  Never run `--suppress-all` or `--suppress-rule` to get a new change through.
- **Types:** `frontend/tsconfig.json` is `strict` with no unused locals or
  parameters and `noImplicitOverride`. `noUncheckedIndexedAccess` is not on yet:
  it raised 521 errors on 2026-10-13, too many for one change; it is a later
  ratchet (section 10).
- **`any`:** not without a comment saying why; `strictTypeChecked`'s
  `no-unsafe-*` and `no-explicit-any` rules enforce it.
- **Visual rules:** colours, type and components follow `DESIGN.md`; do not
  restate them in code comments.

## 5. Size and complexity

Long files are where agents lose track of what a module does. `tools/check_size.py`
(stdlib only) checks `.py`, `.ts` and `.tsx` files under `backend/app`,
`backend/tests`, `tools`, `frontend/src` and `e2e`, skipping `node_modules`,
`fixtures` and `.d.ts` files. Test files are anything under `backend/tests/` or
`e2e/tests/`, `test_*.py`, and `*.test.ts(x)` or `*.spec.ts(x)`.

| | Prefer at most | Warn above | Fail above |
|---|---|---|---|
| Code | 400 lines | 500 | 800 |
| Tests | 600 lines | 750 | 1,200 |

- A warning prints and exits 0. A failure is a file over the fail limit that is
  not in `tools/size_baseline.json`, or a baselined file that grew.
- The baseline holds the 17 files that were already over the fail limit on
  2026-10-13, with their line counts. The numbers may only go down: after
  shrinking one, run `make size-update`, which lowers counts and drops files back
  under the limit, and never raises one.
- It runs in pre-commit on staged files, in `make check`, and in the CI backend
  job. `make size-report` lists everything over the preferred size, biggest first.
- **Functions:** ruff `C901` max-complexity 12, `PLR0915` max-statements 50,
  `PLR0912` max-branches 15; ESLint `complexity` 15, `max-lines-per-function` 120
  (blank lines and comments skipped; components count), `max-depth` 4. Existing
  offenders are baselined by `# noqa` comments and the ESLint suppressions.
- Prettier lengthened seven frontend files on 2026-10-13 (it breaks long JSX and
  call chains over several lines); their counts in the size baseline were taken
  after it ran.

When a file is flagged, split it by responsibility, the way the code is already
split into routers, services and schemas: a service per concept, a component per
piece of UI, helpers beside the code that uses them. Do not split by line count
into `part1`/`part2`, and do not squeeze lines to get under a limit.

## 6. Tests

- Every endpoint has at least one test (from `CLAUDE.md`).
- Test data is invented, following the synthetic rules in `SECURITY.md`; never a
  real receipt, store, address or amount.
- A flaky test is fixed, or quarantined with a linked issue; never retried blindly
  (guideline).
- Tests that need something outside the suite carry a marker so they can be
  deselected: `llm` and `realdata` today (`backend/pyproject.toml`). A
  timing-sensitive test gets a marker of its own (guideline).

## 7. Docs, YAML, shell, Docker and workflows

- **Every file:** UTF-8, LF line endings, a final newline, no trailing whitespace
  (except Markdown line breaks); four-space indents for Python, TOML, shell,
  Dockerfiles and nginx, two for TypeScript, JSON, YAML and CSS
  (`.editorconfig`; pre-commit's `end-of-file-fixer`, `trailing-whitespace` and
  `mixed-line-ending`).
- **YAML, TOML, JSON:** must parse (pre-commit `check-yaml`, `check-toml`,
  `check-json`). `check-yaml` skips the pnpm lockfiles (several documents) and
  `compose.demo.yaml`/`compose.e2e.yaml` (Compose's `!override` tag).
- **Workflows:** actionlint, which also runs shellcheck over `run:` blocks
  (pre-commit and the CI "Repo hygiene" job), and zizmor (both). Actions are pinned
  to a commit SHA with the exact version in a comment; checkouts set
  `persist-credentials: false`; `${{ }}` values reach scripts through `env:`.
- **Markdown:** sentence-case headings, no emoji, prose without hard line-length
  limits; markdownlint-cli2 with a relaxed config (`.markdownlint-cli2.yaml`):
  off are line length, inline HTML, ordered-list numbering, emphasis as a
  heading, fence languages, table pipe spacing, the first-line heading (the PR
  template), and `MD037`/`MD038`, whose fixes change meaning (`MS* > EV-*`, the
  `XMP ` chunk name). Repeated headings are allowed under different parents.
- **Shell and Docker:** shellcheck, and hadolint (`.hadolint.yaml`), which allows
  unpinned distribution packages (`DL3008`, `DL3018`): the base images are pinned,
  and mirrors drop old package versions.
- **Editorconfig:** editorconfig-checker in CI (`.editorconfig-checker.json`)
  checks line endings, final newlines, trailing whitespace and tabs. Indent width
  and line length are left to the formatters: its indent check misreads
  docstrings, SQL and other multi-line strings.

## 8. Commits and pull requests

- Subjects in sentence case that say what changed in plain words, as the history
  does; small commits that each leave the suite green.
- One concern per pull request; pull requests are squash-merged.
- Never `--no-verify`. `make check-pii` before every commit (`SECURITY.md`).
- Formatting-only changes go in their own pull request, never mixed with
  behaviour. After a formatting change merges, its commit goes in
  `.git-blame-ignore-revs` (GitHub's blame reads it; `make setup` sets
  `blame.ignoreRevsFile` locally), and other branches rebase and re-run `make fmt`
  rather than resolving formatting conflicts by hand.
- Before merging a migration, check its number against `main`: parallel branches
  have collided on migration numbers before.

## 9. Dependencies

- Dependabot opens grouped monthly updates (`.github/dependabot.yml`); a batch of
  them is combined into one pull request.
- CI installs from frozen lockfiles: `uv sync --frozen`,
  `pnpm install --frozen-lockfile`.
- Every pre-commit hook has a pinned `rev:`, every Action a SHA, every
  CI-installed tool an exact version (ty, zizmor, pre-commit).
- Upgrade a tool that changes output (ruff, Prettier, ESLint, ty) in its own pull
  request, with the reformat or new count in that pull request.

## 10. Later

Not enforced yet, and planned:

- Coverage floors: `pytest-cov` and Vitest thresholds, ratcheted up.
- Dead-code reports: knip for the frontend, vulture for Python.
- ty as a gate, at the review point in section 3.
- `noUncheckedIndexedAccess` in `frontend/tsconfig.json` (521 errors on
  2026-10-13), once the count is small enough to fix in one change.
