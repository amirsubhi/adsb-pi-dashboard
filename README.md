# ADS-B Pi Dashboard

A single-page live dashboard for a Raspberry Pi (or any Linux box) running
an ADS-B flight receiver. It shows feeder health for FlightRadar24 and
ADSBExchange side by side, system vitals (CPU temperature, under-voltage,
uptime, memory, disk), and a flight history log backed by SQLite — all in
one page on your own network, with no cloud dependency.

![status: live](https://img.shields.io/badge/status-live-brightgreen)
![license: MIT](https://img.shields.io/badge/license-MIT-blue)

## Why

Most feeder setups already expose *some* of this — `fr24feed-status`,
tar1090's map, graphs1090 — but scattered across different tools, ports and
styles, and none of them keep a log of which flights actually passed
overhead. This pulls it into one page and adds that history.

## What it shows

- **System vitals** — CPU temperature, Raspberry Pi under-voltage/throttling
  flags, uptime, load average, memory, disk, receiver gain and clock drift.
- **Feeders** — FlightRadar24 (`fr24feed`) and ADSBExchange
  (`adsbexchange-feed` / `adsbexchange-mlat`) connection status, aircraft
  tracked, and MLAT peer count — each card greys out automatically if that
  feeder isn't installed.
- **Aircraft in range** — the live list from your receiver right now.
- **Flight history** — every aircraft sighting (first seen, last seen,
  duration, max altitude, max speed), logged locally and kept 30 days.
  Filterable by last 2h / 24h / 7 days.
- **Trends** — small sparklines for CPU temperature and aircraft count over
  the last 6 hours.

Everything auto-refreshes in the browser; no external fonts or JS libraries
are loaded, so it keeps working even if the box's internet connection drops
— which is exactly when you'd want it to.

## Requirements

- A Linux host running [readsb](https://github.com/wiedehopf/readsb) or a
  compatible decoder (dump1090-fa, etc.) that writes `aircraft.json` /
  `stats.json` to a local directory (default `/run/readsb`).
- Python 3.7+ (standard library only — nothing to `pip install`).
- `fr24feed` and/or `adsbexchange-feed`/`adsbexchange-mlat` are both
  **optional**. Whichever isn't installed just shows as "Not installed" on
  its card instead of erroring.
- Raspberry Pi firmware tools (`vcgencmd`) are used for CPU temperature and
  under-voltage detection when available, with a generic
  `/sys/class/thermal` fallback on non-Pi boards.

This was built against the official
[ADSBExchange Raspberry Pi image](https://www.adsbexchange.com/how-to-feed/)
with `fr24feed` added on top, but nothing in the code is specific to that
image beyond the default file paths below.

## Install

```bash
git clone https://github.com/amirsubhi/adsb-pi-dashboard.git
cd adsb-pi-dashboard
./install.sh
```

This copies `app.py` and `dashboard.html` into `~/adsb-dashboard`, installs
a systemd service running as your current user, and starts it. The script
prints the dashboard URL (`http://<this host's IP>:8099/`) when done.

To remove it:

```bash
./uninstall.sh
```

### Running it without systemd

```bash
python3 app.py
```

It listens on `0.0.0.0:8099` and writes its SQLite database and reads
`dashboard.html` from `~/adsb-dashboard` by default.

## Configuration

All optional, set as environment variables before starting the service
(edit the `[Service]` block in
`/etc/systemd/system/adsb-dashboard.service` to add `Environment=` lines,
then `sudo systemctl daemon-reload && sudo systemctl restart adsb-dashboard`):

| Variable | Default | Meaning |
|---|---|---|
| `ADSB_DASHBOARD_PORT` | `8099` | Port the dashboard listens on |
| `ADSB_READSB_DIR` | `/run/readsb` | Where `aircraft.json` / `stats.json` live |
| `ADSB_ADSBX_DIR` | `/run/adsbexchange-feed` | Where ADSBExchange's `status.json` / `receiver.json` live |
| `ADSB_POLL_INTERVAL` | `15` | Seconds between samples |
| `ADSB_SESSION_GAP` | `300` | Seconds an aircraft can be absent before its next sighting starts a new history entry |
| `ADSB_RETAIN_DAYS` | `30` | How long history and metrics are kept |
| `ADSB_DATA_DIR` | `~/adsb-dashboard` | Where the SQLite database and `dashboard.html` are read from |

## API

The dashboard is just a client of its own JSON API, so you can build other
things on top of it:

- `GET /api/status` — current snapshot: system vitals, feeder status,
  live aircraft list.
- `GET /api/history?hours=24` — flight sessions with `last_seen` inside the
  last N hours, newest first.
- `GET /api/metrics?hours=6` — raw `(timestamp, temp_c, aircraft_count)`
  samples for the trend sparklines.

All responses are JSON with `Access-Control-Allow-Origin: *`.

## How flight history works

Every `ADSB_POLL_INTERVAL` seconds, the collector reads the receiver's
current aircraft list and, for each one, either extends its most recent
open session (if seen within `ADSB_SESSION_GAP` seconds) or starts a new
row. So a single aircraft passing through twice in a day shows as two
separate history entries, not one. Everything is kept in a single SQLite
file (`history.sqlite`) with no external database required.

## License

MIT — see [LICENSE](LICENSE).
