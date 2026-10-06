# Third-party notices

The dashboard's own code is MIT licensed (see [LICENSE](LICENSE)). The live map
bundles the components below so it works without any CDN. Each one's licence
travels with it in the folder named.

## Bundled

| Component | Version | Licence | Where | Source |
|---|---|---|---|---|
| Leaflet | 1.9.4 | BSD-2-Clause | `vendor/leaflet/` (`LICENSE`) | https://leafletjs.com |
| topojson-client | 3.1.0 | ISC | `vendor/topojson-client/` (`LICENSE`) | https://github.com/topojson/topojson-client |
| world-atlas (`countries-50m.json`) | 2.0.2 | ISC | `geo/` (`LICENSE-world-atlas`) | https://github.com/topojson/world-atlas |
| Natural Earth 1:50m Admin 0 countries (data inside world-atlas) | 4.1.0 | Public domain | `geo/countries-50m.json` | https://www.naturalearthdata.com |

Natural Earth asks for, but doesn't require, a credit; the map shows
"Natural Earth" in its corner. Leaflet is credited there too.

SHA-256 of the bundled files, to make any change to them visible in review:

```
db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a  vendor/leaflet/leaflet.js
a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6  vendor/leaflet/leaflet.css
25cd02ae486cc5063e0215a4e4cfb15de83700c87ac48bac4d57dc6aaf3ebb89  vendor/topojson-client/topojson-client.min.js
04342cdc1e3016bcd7db1630de95684d67b79fe3c8c460321e87aef469502394  geo/countries-50m.json
```

Check them with `sha256sum -c` against this list after updating any of them.

## Online map tiles (not bundled)

When `map_tiles` is `osm` or `carto`, the map loads street-map images from that
provider while the Pi has internet access. They are not redistributed, but
each requires a visible credit, which the map adds once tiles load:

- **OpenStreetMap** (`map_tiles = osm`, the default): map data
  © OpenStreetMap contributors, available under the
  [Open Database Licence](https://www.openstreetmap.org/copyright). The tiles are
  served under the [OSMF tile usage policy](https://operations.osmfoundation.org/policies/tiles/),
  which allows light use like a personal dashboard with attribution.
- **CARTO** (`map_tiles = carto`): © OpenStreetMap contributors © CARTO. Since
  September 2026 CARTO's basemaps need an API key; it is free for
  non-commercial use. See [CARTO's basemap terms](https://carto.com/legal/basemap-terms/)
  and get a key at https://carto.com/basemaps/apikey.

Set `map_tiles = off` to load nothing from the internet; the map then shows
only the bundled Natural Earth outline.

## Not bundled, credited

This dashboard reads the files that [readsb](https://github.com/wiedehopf/readsb)
writes and takes ideas from [tar1090](https://github.com/wiedehopf/tar1090) and
[graphs1090](https://github.com/wiedehopf/graphs1090). No code from them is included.
