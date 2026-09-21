# Map tiles

The map is a MapLibre GL map reading a single PMTiles file served by the `web`
container from `data/tiles/`. Nothing is fetched from an outside origin while the
map is in use. The file is not committed: it is a few hundred megabytes and it is
downloaded or cut once per deployment.

## Obtaining an extract

Protomaps publishes daily planet-wide basemap builds under an ODbL licence (the
data) with their own attribution requirement (the build). The `pmtiles` command
line tool can cut a bounding box out of a build without downloading the whole
planet, because it reads the archive over HTTP range requests.

1. Install the tool: <https://github.com/protomaps/go-pmtiles/releases> (MIT), or
   `brew install pmtiles` on macOS.
2. Find the latest build name at <https://maps.protomaps.com/builds/>.
3. Cut the region. Southern California from the Tehachapis to the border, coast to
   the desert edge, is roughly this box (west, south, east, north):

   ```bash
   mkdir -p data/tiles
   pmtiles extract https://build.protomaps.com/<build>.pmtiles data/tiles/basemap.pmtiles \
     --bbox=-120.9,32.5,-114.1,35.8 --maxzoom=15
   ```

   Adjust the box to your own region; the tests and the synthetic geography in
   `SECURITY.md` assume nothing about it beyond the file being present or absent.
4. Restart `web` (or just reload; nginx serves the file with range requests).

The application looks for `data/tiles/basemap.pmtiles`. If the file is absent the
map renders a plain background with pins in their correct relative positions and
says that tiles are missing; nothing else is blocked.

## Attribution

Every view of the map shows "© OpenStreetMap contributors" and "© Protomaps".
Adopted vendor locations carry OSM identifiers and OSM-derived fields (name,
address, opening hours); they are displayed with the same attribution. See
`docs/licensing.md`.
