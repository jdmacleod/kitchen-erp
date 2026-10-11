# Example recipes

Fifteen public Cooklang recipes, so that a fresh Kitchen ERP deployment has
something to index and cost before a household points `RECIPES_PATH` at its own
repository (`docs/spec/07`, prerequisites). `make seed-examples` copies them into
`data/recipes/`; the demo and e2e Compose stacks mount this directory directly.

Nothing here comes from any household's own recipes. Every file is taken from a
public repository under the MIT licence, listed below with the commit it was
fetched at. The licence texts are in `../LICENSES/`.

| Source repository | Commit | Licence | Files taken |
|---|---|---|---|
| [cooklang/cookcli](https://github.com/cooklang/cookcli), `seed/` | `845a8f2d5165701aef6357047cbb46fd4cebd856` | MIT (`../LICENSES/cookcli.MIT.txt`) | `basics/vinaigrette`, `basics/pizza-dough`, `breakfast/pancakes`, `breakfast/chocolate-toast`, `mains/risotto`, `mains/thai-green-curry`, `mains/neapolitan-pizza`, `sides/caprese` |
| [LiHRaM/cooklang-recipes](https://github.com/LiHRaM/cooklang-recipes), `recipes/` | `6662f2735a9609d846fc6805fc5ff104551ccdfb` | MIT (`../LICENSES/LiHRaM-cooklang-recipes.MIT.txt`) | `basics/self-raising-flour`, `basics/garam-masala`, `baking/beer-bread`, `baking/quick-milk-buns` |
| [Gunde/recipes](https://github.com/Gunde/recipes) | `4ef42b2d72d83b75eca1e6ea958061d06265e05d` | MIT (`../LICENSES/Gunde-recipes.MIT.txt`) | `mains/borsch`, `mains/root-vegetable-tray-bake`, `desserts/mojito-sorbet` |

## What was changed on the way in

- From cookcli only the files its `seed/` sourced to cooklang.org or wrote itself
  were taken. Its files naming a person as `author:` (transcriptions of a
  published cookbook) were left out.
- Where a file transcribed a commercial recipe (the pizza dough and pizza, the
  beer bread, the milk buns, the self-raising flour and garam masala mixes), the
  instruction prose was rewritten in our own words and the third-party URL
  dropped. An ingredient list is not copyrightable; its wording can be.
- Older `>> key: value` metadata was converted to `---` front matter. Every file
  carries `title`, `servings`, `tags` and `source` (the source repository).
- Cooklang recipe references (`@./Shared/Vinaigrette{}`) were replaced with the
  ingredients themselves: Kitchen ERP's parser reads such a reference as an
  ingredient named `./Shared/Vinaigrette`, which would sit in the resolve queue
  for ever.
- Size words became notes (`@tomatoes{3}(large, ripe)` rather than
  `@tomatoes{3%large}`), so the quantity converts. Units like `pinch`, `clove`,
  `sprigs` and `balls` stay as written: the cost table reports those lines as not
  yet convertible, which is what a real repository looks like too.
- Four names were conformed to the standard list's spelling (`cardamom`,
  `distilled vinegar`, `ground sage`, `white wine`). A brand name in one source
  was replaced with the generic `yeast extract`.
- A joke line in the pancakes file (an extra tablespoon of salt "to ruin it")
  was dropped.

## Checking the files

Every file parses with the in-house parser:

```bash
cd backend && uv run python -c "
from pathlib import Path
from app.recipes.cooklang import parse, ParseError
for p in sorted(Path('../examples/recipes').rglob('*.cook')):
    r = parse(p.read_text()); assert not isinstance(r, ParseError), (p, r)
print('ok')"
```

`kerp recipes lint --path examples/recipes` reports against the catalog of the
database it runs on, so on an empty catalog every name is "unknown"; that is
expected, and the resolve page is where they get matched. Two names are flagged
as drift from the standard list and are kept on purpose: `carrots` (the standard
entry is the singular; the indexer matches it through its singular form) and
`cannellini beans` (the standard entry is `dried white beans`; these are tinned).

## Adding one

Add only files whose licence is MIT, CC0 or Apache-2.0, name the source
repository and commit in the table above and the licence text in `../LICENSES/`,
rewrite any prose that transcribes a published recipe, and never add a person's
name, a household's own file, or a store's name. Run `make check-pii` before
committing. `docs/licensing.md` carries the summary row.
