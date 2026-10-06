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

- **Alerts, only when something is wrong.** When all is well the page says
  so in one quiet line. Under-voltage, throttling, a hot CPU or a feeder going
  down appear at the top as red **WARNING** or amber **CAUTION** strips, worst
  first, each saying what to do. This follows the "dark cockpit" idea from
  aircraft displays: no lights when everything is normal.
- **Aircraft in range, in context.** The current count, and whether it's
  busier or quieter than usual for this time of day. Beside it: messages per
  second, today's furthest aircraft and its bearing, and unique aircraft seen
  today, plus the closest and highest aircraft right now.
- **Traffic, last 24 hours** against the usual range for each time of day
  over the past week (the reference band graphs1090 users know). It appears
  after two days of data.
- **Coverage.** The furthest position heard in each 10° direction today,
  against your best of the past 7 days, so you can see where terrain or
  buildings limit your antenna.
- **This Pi and the receiver.** CPU temperature and message rate with small
  6-hour trend lines, power, uptime, load, memory, SD card, gain, signal and
  noise, clock drift.
- **Feeders.** FlightRadar24 (`fr24feed`) and ADSBExchange
  (`adsbexchange-feed` / `adsbexchange-mlat`) status, aircraft tracked and MLAT
  peers. A card greys out if that feeder isn't installed.
- **Flight history.** Every aircraft sighting (first seen, last seen,
  duration, highest altitude, fastest speed), logged locally. Filter by the
  last 2 hours, 24 hours or 7 days.
- **Light and dark themes.** Follows your device, or pick Light or Dark; the
  choice is remembered in that browser.

Altitudes above your country's transition altitude show as flight levels
(`FL350`); set `transition_alt` for your region.

Everything auto-refreshes in the browser; no external fonts or JS libraries
are loaded, so it keeps working even if the box's internet connection drops
— which is exactly when you'd want it to.

## Requirements

- A Linux host running [readsb](https://github.com/wiedehopf/readsb) or a
  compatible decoder (dump1090-fa, etc.) that writes `aircraft.json` /
  `stats.json` to a local directory (default `/run/readsb`).
- Python 3.7+ (standard library only — nothing to `pip install`). Tested on 3.9, 3.11 and 3.13.
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

This copies the program into `~/adsb-dashboard`, creates `settings.ini`
there, installs a systemd service running as your current user, and starts
it. On a first install it asks for your country's transition altitude, and
for the receiver position if readsb doesn't report it. When it finishes it
prints the dashboard URL (`http://<this Pi's IP>:8099/`).

To update, pull the latest code and run `./install.sh` again. Your settings
and flight history are kept.

To remove it:

```bash
./uninstall.sh
```

### Running it without systemd

```bash
python3 app.py
```

It listens on `0.0.0.0:8099` and reads `settings.ini` and its pages from
`~/adsb-dashboard` by default (set `ADSB_DATA_DIR` to use another folder).

## Settings

Settings live in `~/adsb-dashboard/settings.ini`. Every option is commented
out with its default; remove the `#` and change the value, then restart:

```bash
nano ~/adsb-dashboard/settings.ini
sudo systemctl restart adsb-dashboard
```

The **Settings** page (`http://<this Pi's IP>:8099/settings`, linked from the
dashboard footer) shows every value in effect and where it came from, plus
checks for the common setup problems: readsb data missing or stale, no
receiver position, the service unable to read the system journal, low disk
space. The page is read-only on purpose. A page that could change settings
would need a login to be safe on a network, and this dashboard has none.

| Option | Environment variable | Default | Meaning |
|---|---|---|---|
| `port` | `ADSB_DASHBOARD_PORT` | `8099` | Port the dashboard listens on |
| `bind` | `ADSB_BIND` | `0.0.0.0` | Address to listen on; `127.0.0.1` for this Pi only |
| `readsb_dir` | `ADSB_READSB_DIR` | `/run/readsb` | Where `aircraft.json` / `stats.json` live |
| `adsbx_dir` | `ADSB_ADSBX_DIR` | `/run/adsbexchange-feed` | Where ADSBExchange's `status.json` / `receiver.json` live |
| `poll_interval` | `ADSB_POLL_INTERVAL` | `15` | Seconds between samples |
| `session_gap` | `ADSB_SESSION_GAP` | `300` | Seconds an aircraft can be absent before its next sighting starts a new history entry |
| `retain_days` | `ADSB_RETAIN_DAYS` | `30` | How long history and metrics are kept |
| `receiver_lat`, `receiver_lon` | `ADSB_LAT`, `ADSB_LON` | not set | Receiver position, if readsb doesn't report it |
| `transition_alt` | `ADSB_TRANSITION_ALT` | `18000` | Feet above which altitudes show as flight levels (Malaysia 11000, UK 6000) |
| `show_exact_location` | `ADSB_SHOW_EXACT_LOCATION` | `no` | Show exact receiver coordinates instead of rounding to ~1 km |
| `cors_origin` | `ADSB_CORS_ORIGIN` | not set | One other website allowed to read the API |

An environment variable overrides `settings.ini`. If you configured an
older version with `Environment=` lines in the service file, `install.sh`
moves them into `settings.ini` when you update. `ADSB_DATA_DIR`
(default `~/adsb-dashboard`) chooses the data folder itself and can only be
set as an environment variable.

## Security

The dashboard is built for a home network:

- **It has no login. Don't port-forward it to the internet.** For remote
  access, use a VPN such as [Tailscale](https://tailscale.com) or WireGuard.
- Other websites open in your browser can't read its API. Before 1.1 the API
  allowed any site to, which could expose your receiver's location. Use
  `cors_origin` if you build something that needs access.
- The receiver position is rounded to about 1 km on the page unless you turn
  on `show_exact_location`, so screenshots don't give away your address.
- Pages are served with a Content Security Policy that only runs scripts from
  the Pi itself (no inline scripts), plus the usual protective headers, and
  everything shown on a page is escaped.
- The systemd service runs as your user with a read-only view of the system
  (it can only write to its data folder) and no way to gain privileges.

## API

The dashboard is just a client of its own JSON API, so you can build other
things on top of it:

- `GET /api/status` — current snapshot: system vitals, feeder status,
  live aircraft list.
- `GET /api/history?hours=24` — flight sessions with `last_seen` inside the
  last N hours, newest first.
- `GET /api/metrics?hours=6&step=300` — `ts`, `temp_c`, `aircraft_count`,
  `msg_rate` and `max_range_nm` samples. `step` (10 to 3600 seconds) averages
  them into buckets; leave it out for raw samples.
- `GET /api/typical` — the usual aircraft count range for each 15-minute slot
  of the day over the past 7 days (`null` where there's under 2 days of data).
- `GET /api/coverage` — furthest distance in nm for each 10° direction, today
  and best of the past 7 days.
- `GET /api/settings` — settings in effect and the station checks shown on
  the Settings page.

All responses are JSON. An invalid `hours` value returns `400` with an
`error` message.

## Development

Everything is standard-library Python; there is nothing to install.

```bash
# Simulated receiver, so you can work without an antenna
python3 tools/fake_readsb.py --dir /tmp/fake-readsb &

# Dashboard against it, using the repo's own pages
ADSB_DATA_DIR="$PWD" ADSB_READSB_DIR=/tmp/fake-readsb python3 app.py

# Tests
python3 -m unittest discover -s tests -v
shellcheck install.sh uninstall.sh
```

GitHub Actions runs the tests on Python 3.9, 3.11 and 3.13 (the versions
Raspberry Pi OS ships) plus `shellcheck` on every push and pull request.

## How flight history works

Every `ADSB_POLL_INTERVAL` seconds, the collector reads the receiver's
current aircraft list and, for each one, either extends its most recent
open session (if seen within `ADSB_SESSION_GAP` seconds) or starts a new
row. So a single aircraft passing through twice in a day shows as two
separate history entries, not one. Everything is kept in a single SQLite
file (`history.sqlite`) with no external database required.

## License

MIT — see [LICENSE](LICENSE).
