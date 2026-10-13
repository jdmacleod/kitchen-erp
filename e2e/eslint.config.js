// ESLint for the browser tests (CODING_STANDARDS.md, TypeScript and React):
// typescript-eslint's type-checked recommended rules through the project service,
// eslint-plugin-playwright's recommended rules, and eslint-config-prettier last.
// CommonJS, like the rest of this package (the specs use __dirname). Findings that
// predate it are held in eslint-suppressions.json, which may only shrink.
const js = require("@eslint/js");
const prettier = require("eslint-config-prettier");
const playwright = require("eslint-plugin-playwright");
const { defineConfig, globalIgnores } = require("eslint/config");
const tseslint = require("typescript-eslint");

// The plugin marks several rules "warn"; here every rule is an error, so a finding
// either fails `pnpm lint` or sits in the suppressions file, never in between.
const playwrightRecommended = playwright.configs["flat/recommended"];
const playwrightRules = Object.fromEntries(
  Object.entries(playwrightRecommended.rules).map(([rule, level]) => [
    rule,
    level === "warn" ? "error" : level,
  ]),
);

module.exports = defineConfig([
  globalIgnores(["node_modules", "test-results", "playwright-report"]),
  {
    files: ["**/*.ts"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommendedTypeChecked,
      { ...playwrightRecommended, rules: playwrightRules },
    ],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: __dirname,
      },
    },
  },
  prettier,
]);
