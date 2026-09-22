# Licensing

Kitchen ERP is released under the MIT License (see `LICENSE`). Everything below
exists to keep that true.

## Reference projects

Clones of other projects sit beside this repository as design references. They
are gitignored, dockerignored, refused by the commit hook, and skipped by the
PII scanner. Read them to learn how others modelled a problem; do not copy code
from them.

| Project | Upstream | Licence | May we borrow code? |
|---|---|---|---|
| cookcli | github.com/cooklang/cookcli | MIT | Yes, with attribution and the MIT notice preserved. Prefer using `cooklang` as a library dependency rather than vendoring. |
| grocy | github.com/grocy/grocy | MIT | Yes, with attribution. It is PHP, so little is directly reusable. |
| paprika-recipes | github.com/coddingtonbear/paprika-recipes | MIT | Yes, with attribution. |
| kitchenowl | github.com/TomBursch/kitchenowl | AGPL-3.0 | **No.** Ideas only. Any code, however small, would require relicensing this project. |
| mealie | github.com/mealie-recipes/mealie | AGPL-3.0 | **No.** Ideas only. This includes its ingredient parser, its unit and food tables, and its seed data files. |

"Ideas only" means: describe the approach in your own words and implement it
independently. Do not open an AGPL file and translate it line by line; that is a
derivative work. If a specific algorithm from an AGPL project is needed, look for
the paper or standard it implements and work from that.

## Third-party data and services

| Data or service | Licence or terms | Obligation |
|---|---|---|
| OpenStreetMap data (Overpass results, adopted locations, opening hours) | ODbL 1.0 | Attribute "© OpenStreetMap contributors" wherever map data or OSM-derived fields are displayed. Storing adopted attributes in the household's own database is a produced work, not a redistribution, so share-alike does not reach the database. Do not redistribute an OSM-derived dataset without ODbL. |
| Protomaps / PMTiles basemap extract | ODbL for the data; Protomaps basemap tiles require attribution per their terms | Show OSM and Protomaps attribution on the map. The extract is downloaded locally and never committed. |
| Overpass API (optional, off by default) | Public instance fair-use policy | Rate-limit, cache, identify the application in the `User-Agent`, send only public place queries. |
| Nominatim (optional, off by default) | Public usage policy: at most one request per second, a valid `User-Agent`, no bulk geocoding | Same as Overpass. Never send household data in a query beyond the address being geocoded. |
| USDA FoodData Central | Public domain (CC0) | None. Cite the source in the ingredient suggestion UI as a courtesy. |
| Tesseract OCR | Apache-2.0 | Notice preserved in the worker image. |
| Ollama and default model `gpt-oss:20b` | MIT (Ollama), Apache-2.0 (gpt-oss) | Notices preserved. Model weights are downloaded locally, never committed. |
| Cooklang specification and `cooklang` parser crate / bindings | MIT | Notice preserved if vendored; prefer a dependency. |
| `opening-hours-py` (OSM opening_hours parser) | MIT or Apache-2.0 | Notice preserved. Avoid `pyopening-hours`, which wraps `opening_hours.js` and is GPL-3.0. |

## Recipes

Recipes live in a separate repository, `cooklang-recipes`, and are content rather
than code. That repository is **MIT**, the same as this one: a single licence
across both is simpler to reason about than a split, and MIT covers whatever
copyright actually subsists in a recipe. Creative Commons BY is the more
conventional choice for prose and remains a reasonable alternative, but it was
not taken.

What matters more than the licence is what goes in. Its public corpus is entirely
**invented** — recipes written to demonstrate the format and exercise the indexer.
The household's own recipes live in that repository's gitignored `private/`
directory, or in a separate private repository, and are never published. A recipe
collection identifies a household through names and occasions at least as surely
as an address does.

On borrowing recipes: in the US an ingredient list and a bare procedure are not
copyrightable, but the expression around them is — headnotes, prose, the
particular wording of the steps, photographs, and the selection and arrangement
of a collection. A recipe taken from a book is rewritten in the contributor's own
words and the original credited in a required `source` field, which that
repository's checker enforces. Attribution is not a licence; rewriting is what
makes it lawful.

This repository must never embed recipe files; it indexes them by path and commit
from a mounted read-only checkout.

## Dependencies

Python and JavaScript dependencies are pulled in through `uv` and `pnpm` and are
not vendored. Before adding one, check that its licence is MIT, BSD, Apache-2.0,
ISC, MPL-2.0, or similarly permissive. GPL and AGPL libraries are not acceptable
in the application; LGPL is acceptable only as a dynamically linked dependency
that is not modified.
