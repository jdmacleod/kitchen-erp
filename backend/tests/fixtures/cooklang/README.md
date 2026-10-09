# Cooklang canonical test suite (vendored)

`canonical.yaml` and `LICENSE` are copied from the Cooklang specification
repository, https://github.com/cooklang/spec, at commit
`f08832362abec6b16db8eb5ae6b421edd6813989` (2026-09-30), from
`tests/canonical.yaml` and `LICENSE`. The only change is that this
repository's pre-commit hook strips trailing spaces at line ends; the YAML
loads to the same 67 cases, and the U+2009 spaces the suite relies on sit
mid-line and are untouched. The suite is MIT-licensed by the Cooklang
contributors; the notice is the `LICENSE` file beside it, and
`docs/licensing.md` records the terms.

`tests/test_cooklang_canonical.py` runs every case in the file against
`app.recipes.cooklang.parse` through a small adapter that maps this project's
`Recipe` onto the suite's step and item shape. The suite is the gate for every
change to the parser (`07`, 3B, criterion 10).

Deliberate exclusions: none. Every case in the file runs and must pass.

To update: fetch both files at a newer commit, change the commit and date
above, and run the suite.
