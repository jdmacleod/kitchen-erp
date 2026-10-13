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

module.exports = defineConfig([
  globalIgnores(["node_modules", "test-results", "playwright-report"]),
  {
    files: ["**/*.ts"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommendedTypeChecked,
      playwright.configs["flat/recommended"],
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
