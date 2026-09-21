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

Recipes live in a separate repository (`cooklang-recipes`) and are content, not
code. Choose a licence for that repository independently; Creative Commons BY or
BY-SA suits recipe text better than MIT, and family recipes may stay private
regardless. This repository must never embed recipe files; it indexes them by path
and commit from a mounted checkout.

## Dependencies

Python and JavaScript dependencies are pulled in through `uv` and `pnpm` and are
not vendored. Before adding one, check that its licence is MIT, BSD, Apache-2.0,
ISC, MPL-2.0, or similarly permissive. GPL and AGPL libraries are not acceptable
in the application; LGPL is acceptable only as a dynamically linked dependency
that is not modified.
