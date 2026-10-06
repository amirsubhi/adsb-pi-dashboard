# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A live dashboard for a Raspberry Pi ADS-B receiver: feeder health (FlightRadar24, ADSBExchange), system vitals, and a SQLite-backed flight history log. `app.py` is the whole server (Python 3.7+, **standard library only**); `dashboard.html`, `map.html` and `settings.html` are vanilla HTML/CSS pages whose scripts live in `static/`. **No CDNs and no external fonts**, so the pages keep working when the Pi is offline. The only third-party frontend code is bundled under `vendor/` (Leaflet, topojson-client) and `geo/` (Natural Earth outline), each with its licence beside it and an entry with version and SHA-256 in `THIRD_PARTY_NOTICES.md`; update both when adding or upgrading one. Keep both constraints: no pip dependencies, and no scripts loaded from other sites. The one optional exception is street-map tile images on the live map (`map_tiles`).

It is a **LAN-only tool with no login**. Don't add endpoints that change state (settings, deletes, restarts) without first adding authentication and CSRF protection; that is why the settings page is read-only.

## Commands

```bash
# Tests (stdlib unittest; ~5 s)
python3 -m unittest discover -s tests -v
python3 -m unittest tests.test_app.SettingsTest.test_environment_overrides_settings_file   # a single test

# Lint what CI lints
python3 -m py_compile app.py tools/fake_readsb.py
shellcheck install.sh uninstall.sh

# Run locally against a simulated receiver (app.py otherwise reads pages and settings.ini from ~/adsb-dashboard)
python3 tools/fake_readsb.py --dir /tmp/fake-readsb &
ADSB_DATA_DIR="$PWD" ADSB_READSB_DIR=/tmp/fake-readsb python3 app.py    # http://localhost:8099/

# Install / remove as a systemd service on the target host (uses sudo)
./install.sh
./uninstall.sh
```

CI (`.github/workflows/ci.yml`) runs the tests on Python 3.9 / 3.11 / 3.13 and shellcheck. Running with `ADSB_DATA_DIR="$PWD"` creates `history.sqlite` and reads an optional `settings.ini` in the repo (both gitignored).

## Architecture

**Settings** (`SETTINGS` list near the top of `app.py`): each entry is `(key, env var, default, type, description)`. `configure()` resolves every key as environment variable → `[dashboard]` section of `settings.ini` → default, storing values in `CFG`, their origin in `CFG_SOURCE`, and invalid values in `CFG_ERRORS` (reported on the settings page instead of crashing). Code reads `CFG["key"]`, never `os.environ`. `ADSB_DATA_DIR` is the exception: it locates `settings.ini`, so it is env-only. Adding a setting means updating `SETTINGS`, `settings.example.ini` (a test checks every key and env var appears there) and the table in README.md.

**`app.py`** is one process with two parts sharing the module-level `LAST` dict guarded by `LOCK`:

1. **Collector thread** (`collector_loop` → `collect_once`, every `poll_interval` s): builds a full snapshot and replaces `LAST`, then writes to SQLite. Sources: readsb JSON in `readsb_dir`, adsbexchange-feed JSON in `adsbx_dir`, and shell-outs (`vcgencmd`, `fr24feed-status`, `systemctl`, `journalctl`) plus `/proc` and `/sys`. Every reader catches its own errors and degrades to an empty value; a missing feeder must never break the snapshot. Text parsing is split into pure functions (`parse_fr24_text`, `parse_mlat_lines`, `parse_undervoltage_lines`, `service_state`) so tests can feed fixtures from `tests/fixtures/`. The kernel-log under-voltage search is cached for 5 minutes because it is slow on SD cards. After reading, `feeder_states()` reduces each feeder and the receiver to ok / degraded / down / stopped / absent, and `health_alerts()` turns the snapshot into the annunciator's alerts; feeder and receiver problems must persist (`_BAD_SINCE` remembers when each started) and are suppressed for `BOOT_GRACE` after boot, so the alert rules live server-side and are unit-tested in `HealthTest`.
2. **HTTP server** (`ThreadingHTTPServer`, `Handler._route`): `/` and `/settings` serve the HTML files from the data dir (read per request, so edits there show on reload); `/map` serves `map.html`; paths in the `STATIC_FILES` whitelist (URL → path in the data dir, content type, cacheable) are served from `static/`, `vendor/` and `geo/` (add new files there or they 404; vendored ones get a one-day cache); `/api/aircraft` reads `aircraft.json` fresh per request (`live_aircraft`, cached per file mtime); `/api/trails` returns the in-memory `TRAILS` kept by `trail_loop` (a second thread sampling every 2 s, `update_trails` keeps 5 minutes, nothing written to disk); `/api/map-config` gives the receiver position, transition altitude and tile provider; `/api/status` returns `LAST`; `/api/history` and `/api/metrics` (optional `step` averaging) query SQLite; `/api/typical` is the 7-day usual-range band, recomputed at most every 10 minutes (`typical_cached`); `/api/coverage` is per-direction range; `/api/settings` returns `settings_payload()` (settings plus `station_checks()`). All responses go through `_common_headers()` (CSP from `csp()`, nosniff, frame and referrer policy; a CORS header only if `cors_origin` is set). `csp()` adds the tile provider's host to `img-src` only when `map_tiles` uses one; providers are defined in `TILE_PROVIDERS` (OpenStreetMap needs no key, CARTO needs `carto_key` since Sept 2026). Invalid query input raises `BadRequest` → 400; any other exception → 500 JSON (`hours_param()`, `step_param()`). The CSP is `script-src 'self'`, so **pages must not contain inline `<script>` or `on*=` handlers** (a test checks); styles may stay inline.

**Database**: `init_db()` runs once at startup, sets WAL mode, and applies migrations keyed on `PRAGMA user_version` (`SCHEMA_VERSION`). There is no other migration system, so a schema change means bumping `SCHEMA_VERSION` and adding an `if version < N:` step that upgrades an existing database in place, plus a test in `HistoryTest`. `get_conn()` only connects; each request or collection uses its own short-lived connection.

**Data model**: a `sessions` row (`upsert_sessions`) is one continuous sighting of an aircraft `hex`; if the latest row for that hex was seen within `session_gap` seconds it is extended (max alt/speed, `samples`+1), otherwise a new row starts. `metrics` stores one row per poll (`temp_c`, `aircraft_count`, `load1`, `msg_rate`, `max_range_nm`). `coverage` keeps the furthest distance per local day and 10° sector, and `daily` each day's furthest aircraft (`update_coverage`); `today_summary` derives "unique today" and "furthest today" from them. Distances need the receiver position (`receiver_location()`: receiver.json, else settings); positions older than `POSITION_MAX_AGE` or beyond `MAX_PLAUSIBLE_NM` are ignored. "Today" and the typical-band slots use the Pi's local time. Everything is pruned to `retain_days` each cycle.

**Pages** are pure clients of the JSON API. The map (`static/map.js`) polls `/api/aircraft` every second and dead-reckons positions between polls (capped at 15 s), draws the Natural Earth outline in a custom Leaflet pane *below* the tile pane so online tiles cover it and offline it shows through, and only adds the tile credit once a tile has loaded. `static/theme.js` is loaded in every page's `<head>` (applies the saved Auto/Light/Dark choice before paint, shared via localStorage key `adsb-theme`); colours are CSS variables with light values on `:root` and dark values under both `prefers-color-scheme` and `[data-theme="dark"]`, and charts are SVG styled by those variables, so a theme switch needs no redraw. Field names in `collect_once`'s snapshot are an implicit contract with `dashboard.html` (e.g. the annunciator draws `alerts` and the feeder cards follow `feeders.<name>.state`), so change both together. Anything from the receiver, a feeder or the journal goes through `esc()` before `innerHTML`. The receiver position is shown rounded unless `station.show_exact_location` is true.

## Deployment

`install.sh` copies `app.py`, the HTML pages, `static/*.js`, `vendor/`, `geo/` and `settings.example.ini` into `${ADSB_DATA_DIR:-~/adsb-dashboard}` (update that `cp` line when adding a file the server needs), creates `settings.ini` on first install (moving any `Environment=ADSB_*` lines from a pre-1.1 service file into it, and asking for transition altitude and, if readsb doesn't report it, the receiver position), and renders `systemd/adsb-dashboard.service.template` with an inline Python script (placeholders `__USER__`, `__INSTALL_DIR__`, `__PYTHON__`, `__GROUPS__`, `__ENVIRONMENT__`). The unit is sandboxed (`ProtectSystem=strict` with the data dir as the only writable path, `NoNewPrivileges`) and gets the `video` / `systemd-journal` / `adm` groups that exist so `vcgencmd` and `journalctl` work. The service runs from the copied files, so re-run `install.sh` after changing anything; it restarts the service and keeps `settings.ini` and the database.

User-visible changes get an entry in `CHANGELOG.md` and a bump of `VERSION` in `app.py` (shown on the settings page). README.md is the user guide (install, using each page, settings table, troubleshooting); `CONTRIBUTING.md` and `SECURITY.md` hold the project rules and security stance. README screenshots live in `docs/screenshots/` and are taken with `tools/fake_readsb.py`.

`.gitattributes` forces LF endings for `.sh`, `.py`, and the service template (they must run on Linux).
