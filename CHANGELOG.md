# Changelog

Notable changes to the dashboard. To update an installed copy, pull and run
`./install.sh` again; settings and history are kept and the database is
upgraded in place.

## 1.4.0: Feeder alerts you can trust

- Fixed: a stopped fr24feed showed as running, because "not running" contains
  "running". The fr24feed-status output is now read line by line, with or
  without the `[ ok ]` / `[FAIL]` markers.
- Fixed: losing the link to FlightRadar24 went unnoticed while fr24feed
  still read the receiver. The card and alerts now check the link, the
  receiver and MLAT separately.
- Fixed: ADSBExchange MLAT figures could come from a log line hours old. Only
  the last 30 minutes count now, and the MLAT service state is checked.
- New alerts: no data from the receiver (readsb stalled for a minute), MLAT
  not working (FlightRadar24 or ADSBExchange), SD card nearly full, and
  collector errors.
- Feeder alerts wait out short blips (90 s to 2 min, 10 min for MLAT) and stay
  quiet for five minutes after boot, so restarts don't raise false alarms.
  The alert logic now lives in `app.py`; `/api/status` has new `alerts` and
  `feeders` fields.
- A row of four status tiles under the alerts (FlightRadar24, ADSBExchange,
  Receiver, This Pi; two by two on a phone) shows whether each part of the
  station is working, with the reason when it isn't. The feeder figures move
  to a Details section further down.

## 1.3.0: Live map

- New live map at `/map`: aircraft move smoothly between updates, coloured by
  altitude with 5-minute trails; a searchable, sortable list; a details card;
  emergency squawks pinned and shown in red; range rings; and a Live
  indicator that warns when data stops arriving.
- Street maps from OpenStreetMap by default, or CARTO's light and dark maps
  with a free key (`map_tiles`, `carto_key`). A bundled Natural Earth outline
  sits underneath, so the map works offline.
- New API endpoints `/api/aircraft`, `/api/trails` and `/api/map-config`.
- Leaflet, topojson-client and the coastline data are bundled with their
  licences; see `THIRD_PARTY_NOTICES.md`.

## 1.2.0: Dashboard redesign

- Alerts appear at the top only when something is wrong; a normal day shows a
  single line.
- The aircraft count is compared with the usual range for the time of day,
  alongside messages per second, today's furthest aircraft and unique
  aircraft today.
- 24-hour traffic chart against the usual range over the past week, and a
  coverage plot of the furthest range in each direction.
- Auto, Light and Dark themes on every page.
- Altitudes above `transition_alt` show as flight levels.
- Database schema 2: message rate, furthest range, coverage and daily
  records. New endpoints `/api/typical` and `/api/coverage`, and a `step`
  option on `/api/metrics`.
- Page scripts moved to separate files, so the Content Security Policy no
  longer allows inline scripts.

## 1.1.0: Settings file and hardening

- Settings move to `~/adsb-dashboard/settings.ini`; the installer creates it
  and carries over `Environment=` lines from older service files.
- New read-only Settings page with station checks.
- The API no longer sends `Access-Control-Allow-Origin: *`, so other websites
  can't read it (and your receiver's location). New `cors_origin` option.
- The receiver position is rounded on the page unless `show_exact_location`
  is on.
- Content Security Policy and other protective headers; receiver text is
  escaped; invalid query values return 400.
- The systemd service is sandboxed and gets the groups it needs for
  `vcgencmd` and the journal.
- Fixes: a custom data folder now reaches the service; under-voltage no
  longer shows "--" when there are no events; ADSBExchange no longer shows
  "Down" when it isn't installed; the installer's URL is no longer mangled.
- SQLite WAL mode and schema versioning.
- Tests, a simulated receiver (`tools/fake_readsb.py`) and CI.

## 1.0.0

- First release: system vitals, FlightRadar24 and ADSBExchange status,
  aircraft in range, and a 30-day flight history in SQLite.
