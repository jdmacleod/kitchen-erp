# Coding standards

How code in this repository is formatted, linted, sized and reviewed, and which
tool checks each rule. Most of the code is written by agents working in parallel
branches, so the rules live in tools wherever possible and this file explains
them. A rule no tool checks is marked as a guideline.

Some rules arrive with the formatting PR, a single change that reformats the tree
when no other pull requests are open. Until it merges they are the standard for
new code but nothing fails on them; each is marked "(enforced from the formatting
PR)".

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
| Format hook | After every Claude Code edit | `ruff format` and import sorting on the edited Python file | `.claude/settings.json`, `tools/format_edited.sh` |
| Pre-commit | Every commit, staged files | Hygiene, gitleaks, ruff, the size check, actionlint, the PII scanner | `.pre-commit-config.yaml` |
| CI | Every push and pull request | Full lint, tests, size, types (advisory), repo hygiene, e2e, PII safeguards, CodeQL | `.github/workflows/` |

The format hook reads the edited path from the hook's JSON, formats `backend/` and
`tools/` Python from the repository root (so the result matches pre-commit), stays
silent, and always exits 0: a formatter that cannot run never blocks an edit.
Frontend and e2e files get Prettier the same way (enforced from the formatting PR).

The commands, from the repository root:

| Command | Does |
|---|---|
| `make fmt` | Format: ruff over `backend/` and `tools/`; Prettier over `frontend/` and `e2e/` (enforced from the formatting PR) |
| `make lint` | `ruff check` over `backend/` and `tools/`, ESLint over `frontend/` |
| `make types` | ty over the backend; reports, never fails |
| `make size` | The size check; `make size-report` lists files over the preferred size; `make size-update` lowers the baseline |
| `make check` | The safeguards, the size check and every pre-commit hook over all files |
| `pre-commit run --files <changed>` | The commit hooks on just what you changed |

Before finishing a change, run `make fmt lint` or `pre-commit run --files` on the
changed files. Never hand-format against the formatter: if its output looks wrong,
restructure the code, or raise it in the pull request.

## 3. Python

- **Formatting:** ruff format, line length 100, double quotes
  (`backend/pyproject.toml`, `[tool.ruff]`). `tools/` uses ruff's defaults today
  and the backend's configuration from the formatting PR, through a
  `tools/ruff.toml` that extends it.
- **Lint:** ruff check with `E, F, I, B, UP, C4, SIM, PLW`. The formatting PR
  widens it to add `W, PLE, PLR0912, PLR0915, C90, RUF, ASYNC, PT, S, TID, PTH, ERA`
  (`S101` allowed in tests), with today's complexity offenders listed by
  `# noqa` comments that may only be removed (enforced from the formatting PR).
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

- **Formatting:** Prettier with `printWidth` 100, double quotes and trailing
  commas everywhere, from one `.prettierrc.json` shared by `frontend/` and `e2e/`
  (enforced from the formatting PR). Until then, match the file you are in.
- **Lint today:** ESLint flat config in `frontend/eslint.config.js`:
  `@eslint/js` recommended, `typescript-eslint` recommended, `react-hooks`.
- **Lint from the formatting PR:** `strictTypeChecked` and `stylisticTypeChecked`
  with `parserOptions.projectService`, `jsx-a11y`, `import-x` ordering, and
  `eslint-config-prettier` last; `pnpm lint` runs with `--max-warnings=0`; today's
  violations are held in `eslint-suppressions.json`, which may only shrink
  (enforced from the formatting PR). `e2e/` gets its own `tsconfig.json` and
  ESLint config (enforced from the formatting PR).
- **Types:** `frontend/tsconfig.json` is `strict` with no unused locals or
  parameters; `tsc --noEmit` runs in `pnpm build`. The formatting PR adds
  `noUncheckedIndexedAccess` and `noImplicitOverride`, ratcheted if they produce
  many errors.
- **`any`:** not without a comment saying why (guideline today; ESLint from the
  formatting PR).
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
- **Functions** (enforced from the formatting PR): ruff `C901` max-complexity 12,
  `PLR0915` max-statements 50, `PLR0912` max-branches 15; ESLint `complexity` 15,
  `max-lines-per-function` 120 (components count), `max-depth` 4. Existing
  offenders are baselined.

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
  (pre-commit and the CI "Repo hygiene" job), and zizmor (CI). Actions are pinned
  to a commit SHA with the exact version in a comment; checkouts set
  `persist-credentials: false`; `${{ }}` values reach scripts through `env:`.
- **Markdown:** sentence-case headings, no emoji, prose without hard line-length
  limits; markdownlint with a relaxed config (enforced from the formatting PR).
- **Shell and Docker:** shellcheck and hadolint (enforced from the formatting PR).

## 8. Commits and pull requests

- Subjects in sentence case that say what changed in plain words, as the history
  does; small commits that each leave the suite green.
- One concern per pull request; pull requests are squash-merged.
- Never `--no-verify`. `make check-pii` before every commit (`SECURITY.md`).
- Formatting-only changes go in their own pull request, never mixed with
  behaviour. After a formatting change merges, its commit goes in
  `.git-blame-ignore-revs`, and other branches rebase and re-run `make fmt`
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
