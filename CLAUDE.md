# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A live dashboard for a Raspberry Pi ADS-B receiver: feeder health (FlightRadar24, ADSBExchange), system vitals, and a SQLite-backed flight history log. The whole app is two files: `app.py` (Python 3.7+, **standard library only**) and `dashboard.html` (vanilla JS/CSS, **no external fonts, libraries, or CDNs**, so it keeps working when the Pi is offline). Keep both constraints: don't add pip dependencies or third-party frontend assets.

## Commands

There is no build step, test suite, or linter configured.

```bash
# Run locally against the repo copy of dashboard.html (app.py otherwise reads it from ~/adsb-dashboard)
ADSB_DATA_DIR="$PWD" python3 app.py          # serves http://localhost:8099/

# Quick syntax check
python3 -m py_compile app.py

# Exercise the API
curl -s localhost:8099/api/status
curl -s 'localhost:8099/api/history?hours=24'
curl -s 'localhost:8099/api/metrics?hours=6'

# Install / remove as a systemd service on the target host (uses sudo)
./install.sh
./uninstall.sh
```

The app runs fine on a non-Pi dev machine: every data source is optional and returns `None` / `{}` / `{"running": False}` when its tool or file is missing, so you'll just see empty data. Point `ADSB_READSB_DIR` at a directory containing a sample `aircraft.json` / `stats.json` to get aircraft data. Running with `ADSB_DATA_DIR="$PWD"` creates `history.sqlite` in the repo (gitignored).

## Architecture

**`app.py`** is a single process with two parts sharing state via the module-level `LAST` dict guarded by `LOCK`:

1. **Collector thread** (`collector_loop` → `collect_once`, every `ADSB_POLL_INTERVAL`s): gathers a full snapshot from many sources and replaces `LAST`, then writes to SQLite. Sources:
   - readsb JSON files in `ADSB_READSB_DIR` (`aircraft.json`, `stats.json`, `receiver.json`)
   - adsbexchange-feed JSON in `ADSB_ADSBX_DIR` (`status.json`, `receiver.json`)
   - shell-outs: `vcgencmd` (temp, throttle flags), `fr24feed-status` (regex-parsed text), `systemctl is-active`, `journalctl` (MLAT stats, kernel under-voltage events)
   - `/proc`, `/sys/class/thermal`, `shutil.disk_usage`
   
   Each reader wraps everything in `try/except` and degrades to an empty value — preserve this pattern; a missing feeder must never break the snapshot. Exceptions escaping `collect_once` are stored in `LAST["error"]`.

2. **HTTP server** (`ThreadingHTTPServer`, `Handler.do_GET`): `/` serves `dashboard.html` from `ADSB_DATA_DIR` (read on every request, so HTML edits in the install dir show on reload without restart); `/api/status` returns `LAST`; `/api/history` and `/api/metrics` query SQLite directly. Each request/collection opens its own short-lived connection via `get_conn()`, which also creates the schema (`CREATE TABLE IF NOT EXISTS`) — there is no migration system.

**Flight history model** (`upsert_sessions`): a `sessions` row is one continuous sighting of an aircraft `hex`. If the latest row for that hex was seen within `ADSB_SESSION_GAP` seconds, it's extended (max alt/speed updated, `samples` incremented); otherwise a new row starts. The `metrics` table stores one `(ts, temp_c, aircraft_count, load1)` row per poll for sparklines. Both tables are pruned to `ADSB_RETAIN_DAYS` each cycle.

**`dashboard.html`** is a pure client of the JSON API: `refreshStatus` (12s), `refreshMetrics` and `refreshHistory` (60s). Feeder cards grey out based on fields in `/api/status` (e.g. `fr24.running`, `adsbx_feed_active`), so field names in `collect_once`'s snapshot are an implicit contract with the HTML — change both together. The API is also documented in README.md as public, with CORS `*`.

## Deployment

`install.sh` copies `app.py` + `dashboard.html` into `${ADSB_DATA_DIR:-~/adsb-dashboard}` and renders `systemd/adsb-dashboard.service.template` by `sed`-substituting `__USER__`, `__INSTALL_DIR__`, `__PYTHON__`. The service runs from the copied files, not the repo — re-run `install.sh` (or copy files) and `sudo systemctl restart adsb-dashboard` after editing `app.py`. Configuration is via the `ADSB_*` env vars documented in the `app.py` docstring and README; keep those two lists in sync when adding a variable.

`.gitattributes` forces LF endings for `.sh`, `.py`, and the service template (they must run on Linux).
