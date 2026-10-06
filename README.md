# ADS-B Pi Dashboard

A dashboard and live map for a Raspberry Pi ADS-B receiver. It shows whether
your station is healthy, how it's performing today against a normal day, where
your antenna can hear, and every aircraft overhead, all from the Pi itself on
your home network.

[![CI](https://github.com/amirsubhi/adsb-pi-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/amirsubhi/adsb-pi-dashboard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![Python 3.7+](https://img.shields.io/badge/python-3.7%2B-blue)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/dashboard-dark.png">
  <img alt="The dashboard: an all-clear line, 32 aircraft in range compared with the usual range for the time of day, messages per second, furthest aircraft today, a 24-hour traffic chart against the 7-day usual band, and a coverage plot showing the furthest range in each direction." src="docs/screenshots/dashboard-light.png">
</picture>

<sub>Screenshots use the bundled receiver simulator and sample history.</sub>

- **One Python file, nothing to install.** Standard library only, no pip and
  no database server. Flight history lives in a single SQLite file.
- **Works offline.** Every script and the map's coastline outline are served
  from the Pi, so the pages keep working when the internet doesn't.
- **Feeder health in one place.** FlightRadar24 and ADSBExchange status sit
  beside the Pi's power, temperature and receiver figures.
- **Quiet until something is wrong.** Problems appear as warnings at the top
  of the page; a normal day shows a single line.

## Contents

- [What you get](#what-you-get)
- [Quick start](#quick-start)
- [Requirements](#requirements)
- [Installing, updating and removing](#installing-updating-and-removing)
- [Using it](#using-it)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)
- [Privacy and security](#privacy-and-security)
- [API](#api)
- [Development](#development)
- [Credits and licence](#credits-and-licence)

## What you get

### Dashboard (`/`)

- **Alerts at the top, only when needed:** under-voltage, CPU throttling, a
  hot CPU, or a feeder that has gone down.
- **Aircraft in range, in context:** the current count and whether that's
  busier or quieter than usual for this time of day. Beside it: messages per
  second, today's furthest aircraft and its bearing, and unique aircraft seen
  today.
- **Traffic, last 24 hours,** against the usual range for each time of day
  over the past week.
- **Coverage:** the furthest position heard in each 10° direction, today
  against your best of the past week. Hills and buildings show up as dents.
- **This Pi and the receiver:** temperature and message rate with 6-hour
  trend lines, power, uptime, load, memory, SD card, gain, signal and noise,
  clock drift.
- **Feeders:** FlightRadar24 and ADSBExchange status, aircraft and MLAT peers.
- **Flight history:** every sighting with first and last seen, duration,
  highest altitude and fastest speed, for the last 2 hours, 24 hours or 7 days.

### Live map (`/map`)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/map-dark.png">
  <img alt="The live map: aircraft icons coloured by altitude with short trails and callsign labels, range rings at 50 to 200 nautical miles around the receiver, a searchable aircraft list on the left and a details card for the selected aircraft." src="docs/screenshots/map-light.png">
</picture>

- Aircraft move smoothly between updates. Icons point along each aircraft's
  track, are coloured by altitude, and leave a 5-minute trail.
- A list you can search by callsign, hex code, registration, type or squawk,
  and sort by distance, altitude or callsign.
- Click a plane or a row for details: altitude, climb or descent rate, ground
  speed, track, distance and bearing from you, squawk and signal strength.
- Emergency squawks (7500 hijack, 7600 radio failure, 7700 general emergency)
  are pinned to the top and drawn in red.
- Range rings at 50, 100, 150 and 200 nm, and a **Live** indicator that warns
  when data stops arriving.
- A street map from OpenStreetMap when the Pi has internet, with a bundled
  coastline outline underneath for when it doesn't.

### Settings and station checks (`/settings`)

Every setting in effect and where it came from, plus checks for the usual
setup problems: receiver data missing or stale, no receiver position, the
service unable to read the system journal, low disk space. The page is
read-only; see [Settings](#settings) for why and how to change things.

All pages have **Auto / Light / Dark** themes and work on phones.

## Quick start

On the Pi that runs your receiver:

```bash
git clone https://github.com/amirsubhi/adsb-pi-dashboard.git
cd adsb-pi-dashboard
./install.sh
```

The installer asks two questions (your transition altitude, and your receiver
position if readsb doesn't report it), starts the service, and prints the
address to open, for example `http://192.168.1.50:8099/`.

## Requirements

- **A receiver decoder that writes JSON files:** [readsb](https://github.com/wiedehopf/readsb)
  (default folder `/run/readsb`) or dump1090-fa (`/run/dump1090-fa`; set
  `readsb_dir`).
- **Python 3.7 or newer.** Tested on 3.9, 3.11 and 3.13, the versions shipped
  with Raspberry Pi OS Bullseye, Bookworm and Trixie.
- **Linux with systemd** for the service. Without systemd you can still run
  `python3 app.py` yourself.
- **Optional:** `fr24feed`, `adsbexchange-feed` / `adsbexchange-mlat`. A
  feeder that isn't installed shows as "Not installed" instead of an error.
- **Optional:** Raspberry Pi firmware tools (`vcgencmd`) for under-voltage and
  throttling checks. Other boards fall back to the kernel's temperature sensor.

It was built on the [ADSBExchange Raspberry Pi image](https://www.adsbexchange.com/how-to-feed/)
with `fr24feed` added, but nothing depends on that image beyond default paths.

## Installing, updating and removing

`./install.sh` does the following, as your normal user, using `sudo` only for
the systemd steps:

1. Copies the program into `~/adsb-dashboard` (or `$ADSB_DATA_DIR`).
2. On the first install, creates `~/adsb-dashboard/settings.ini` and asks
   for your transition altitude and, if needed, receiver position. Settings
   from an older version's service file are moved into it.
3. Installs and starts the `adsb-dashboard` systemd service under your user,
   sandboxed so it can only write to its own folder.
4. Prints the dashboard address, and a note if the Pi's clock is on UTC.

**Update** by pulling and running the installer again. Your settings and
flight history are kept, and the database is upgraded in place.

```bash
cd adsb-pi-dashboard
git pull
./install.sh
```

**Remove** the service with `./uninstall.sh`. It leaves `~/adsb-dashboard`
(settings and history) in place; delete that folder too if you want them gone.

**Without systemd,** run it directly. It listens on port 8099 and uses
`~/adsb-dashboard` for its files:

```bash
python3 app.py
```

## Using it

### Reading the dashboard

When everything is fine, the line under the header reads *All checks normal:
power, temperature and feeders.* Otherwise one strip appears per problem,
worst first:

| Strip | Meaning | What to do |
|---|---|---|
| WARNING · Under-voltage now | The Pi isn't getting enough power right now | Use a proper 5 V / 3 A supply and a short, thick cable |
| WARNING · CPU is being throttled | The firmware has slowed the CPU | Check power and cooling |
| WARNING · feed disconnected / down | FlightRadar24 or ADSBExchange stopped feeding | `systemctl status fr24feed` or `adsbexchange-feed` |
| CAUTION · CPU at 75 °C | Running hot (75 °C or more) | Add a heatsink or fan, or improve airflow |
| CAUTION · CPU running warm | 70 °C or more | Keep an eye on it, especially in hot weather |
| CAUTION · Power dipped earlier this boot | Under-voltage happened since the last reboot | Same as under-voltage; it may come back |

**Busier or quieter than usual** compares the current aircraft count with the
range seen at the same time of day over the past week. The grey band on the
traffic chart is that same range; it appears once there are two days of data.

**Coverage** draws the furthest position heard in each 10° direction. The blue
shape is today; the grey outline is your best day this week. A dent that is
always there usually means something blocks the antenna in that direction.

**Flight levels.** Altitudes above your transition altitude show as flight
levels (`FL350` = 35,000 ft), the way pilots and controllers say them. Set
`transition_alt` for your country; the installer asks for it.

### Using the live map

- **Click** a plane for its details, or a list row to also move the map to
  it (rows open with **Enter** too). The × or a click on empty map closes the card.
- **Search** matches callsign, hex code, registration, type or squawk.
- **Trails, Labels, Range rings** switch those layers; your choices are
  remembered in that browser.
- **Recenter** returns to your receiver.
- **Colours** show altitude, from orange near the ground to purple at
  40,000 ft; the legend is in the corner. Red always means an emergency squawk.
- A faded plane hasn't sent a position for 15 seconds. Planes with no
  position for a minute leave the map but stay in the list.
- **Live** turns red and counts the seconds if data stops arriving.

Registration and aircraft type appear when readsb runs with an aircraft
database (`--db-file`); otherwise the list shows the hex code.

## Settings

Settings live in `~/adsb-dashboard/settings.ini`. Every option is listed there
with its default, commented out. To change one:

```bash
nano ~/adsb-dashboard/settings.ini      # remove the # and set the value
sudo systemctl restart adsb-dashboard
```

Then open **Settings** in the dashboard to check the new value and where it
came from.

| Option | Default | What it does |
|---|---|---|
| `port` | `8099` | Port the dashboard listens on |
| `bind` | `0.0.0.0` | Address to listen on; `127.0.0.1` for this Pi only |
| `readsb_dir` | `/run/readsb` | Folder with `aircraft.json` and `stats.json` |
| `adsbx_dir` | `/run/adsbexchange-feed` | Folder with ADSBExchange's `status.json` |
| `poll_interval` | `15` | Seconds between samples for the dashboard and history |
| `session_gap` | `300` | Seconds an aircraft can be out of range before its next sighting counts as new |
| `retain_days` | `30` | Days of history and statistics to keep |
| `receiver_lat`, `receiver_lon` | not set | Receiver position, if readsb doesn't report it |
| `transition_alt` | `18000` | Feet above which altitudes show as flight levels (Malaysia 11000, UK 6000, USA 18000) |
| `show_exact_location` | `no` | Show exact receiver coordinates instead of rounding to about 1 km |
| `map_tiles` | `osm` | Live map background: `osm`, `carto` or `off` |
| `carto_key` | not set | Free CARTO key, for `map_tiles = carto` |
| `cors_origin` | not set | One other website allowed to read the API |

Each option also has an environment variable (shown on the Settings page and
in `settings.ini`) that overrides the file. `ADSB_DATA_DIR` chooses the data
folder and can only be set that way.

> [!NOTE]
> **Why the settings page is read-only.** The dashboard has no login. A page
> that could change settings could be used by anything on your network, and
> even by a website open in your browser. Editing a file over SSH avoids that.

### Map backgrounds

- **`osm`** (default): OpenStreetMap's standard map. No key needed; darkened
  in dark mode.
- **`carto`**: CARTO's purpose-made light and dark maps. Since September 2026
  these need an API key, free for personal use: request one at
  [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey), then set
  `map_tiles = carto` and `carto_key = <your key>`.
- **`off`**: only the bundled coastline outline. Nothing is loaded from the
  internet.

The outline is always there underneath, so the map still works when the Pi
is offline.

## Troubleshooting

Open **Settings** first. Its station checks name most problems directly. The
service log is the next stop:

```bash
journalctl -u adsb-dashboard -f
```

<details>
<summary><b>"Receiver data: no aircraft.json" or the aircraft list is empty</b></summary>

The dashboard can't find your decoder's output. Look for it:

```bash
ls /run/readsb /run/dump1090-fa 2>/dev/null
```

Set `readsb_dir` to the folder that contains `aircraft.json`, then restart.
If the file exists but is old, your decoder isn't running:
`systemctl status readsb` (or `dump1090-fa`).
</details>

<details>
<summary><b>No distances, range rings or coverage</b></summary>

These need the receiver's position. Most readsb setups report it; if yours
doesn't, set `receiver_lat` and `receiver_lon` in `settings.ini`.
</details>

<details>
<summary><b>MLAT figures stay empty, or "System journal" shows a warning</b></summary>

The service reads MLAT figures from the system journal. Run `./install.sh`
again: it gives the service the `systemd-journal` and `adm` groups when they
exist on your system.
</details>

<details>
<summary><b>"Pi firmware tools" warning, or power always shows "—"</b></summary>

`vcgencmd` needs the `video` group, which the installer adds when it exists.
Run `./install.sh` again. On boards other than a Raspberry Pi this check is
simply off.
</details>

<details>
<summary><b>A feeder shows "Not installed" but it is installed</b></summary>

The dashboard looks for the systemd services `adsbexchange-feed` and
`adsbexchange-mlat`, and for the `fr24feed-status` command. If your system
names them differently, please open an issue with the names.
</details>

<details>
<summary><b>The live map shows only the outline, no streets</b></summary>

The Pi has no internet access, `map_tiles` is `off`, or the browser can't
reach the tile server. With `map_tiles = carto`, tiles reading "API KEY
REQUIRED" mean `carto_key` is missing or wrong.
</details>

<details>
<summary><b>Times are wrong, or "today" resets at the wrong hour</b></summary>

The Pi's clock is probably on UTC. Set your time zone and restart:

```bash
sudo timedatectl set-timezone Asia/Kuala_Lumpur
sudo systemctl restart adsb-dashboard
```
</details>

<details>
<summary><b>No grey "usual range" band on the traffic chart</b></summary>

It needs two days of history for each time of day, so it appears after the
dashboard has been running for two days.
</details>

## Privacy and security

The dashboard is meant for a home network:

- **It has no login, so don't port-forward it to the internet.** For access
  from outside, use a VPN such as [Tailscale](https://tailscale.com) or WireGuard.
- Other websites open in your browser can't read its data.
- The receiver position is rounded to about 1 km on the pages, so screenshots
  don't reveal your address. Set `show_exact_location = yes` to change that.
- Every script comes from the Pi itself. The only outside requests are the
  live map's street tiles, which let OpenStreetMap or CARTO see your IP address
  and the area you're viewing; `map_tiles = off` stops them.
- The service runs as your user in a systemd sandbox that can only write to
  its own folder.

See [SECURITY.md](SECURITY.md) for details and how to report a problem.

## API

The pages read the same JSON API that you can use for your own projects.

<details>
<summary>Endpoints</summary>

| Endpoint | Returns |
|---|---|
| `GET /api/status` | Current snapshot: Pi vitals, receiver figures, feeders, aircraft, message rate, unique aircraft and furthest aircraft today |
| `GET /api/history?hours=24` | Sightings with `last_seen` in the last N hours, newest first (up to 400) |
| `GET /api/metrics?hours=6&step=300` | Samples of `temp_c`, `aircraft_count`, `msg_rate`, `max_range_nm`; `step` (10 to 3600 s) averages them |
| `GET /api/typical` | Usual aircraft-count range per 15-minute slot over the past 7 days (`null` where under 2 days of data) |
| `GET /api/coverage` | Furthest distance in nm per 10° direction, today and best of the past 7 days |
| `GET /api/aircraft` | Live aircraft from `aircraft.json`: position, altitude (`"ground"` on the ground), speed, track, vertical rate, squawk, signal, registration and type |
| `GET /api/trails` | The last 5 minutes of positions per aircraft, as `[lat, lon, altitude, time]` |
| `GET /api/map-config` | Receiver position, transition altitude and map background for the map page |
| `GET /api/settings` | Settings in effect and the station checks |

Responses are JSON. An invalid parameter returns `400` with an `error`
message. Browsers on other sites can't read the API unless you set
`cors_origin`.
</details>

## Development

Everything is standard-library Python, so there's nothing to install. A
simulated receiver lets you work without an antenna:

```bash
python3 tools/fake_readsb.py --dir /tmp/fake-readsb &
ADSB_DATA_DIR="$PWD" ADSB_READSB_DIR=/tmp/fake-readsb python3 app.py
# open http://localhost:8099/

python3 -m unittest discover -s tests -v
shellcheck install.sh uninstall.sh
```

GitHub Actions runs the tests on Python 3.9, 3.11 and 3.13 and checks the
shell scripts on every push and pull request. See [CONTRIBUTING.md](CONTRIBUTING.md)
for the project's rules and [CHANGELOG.md](CHANGELOG.md) for what changed in
each version.

<details>
<summary>Project layout</summary>

```
app.py                  the server: collector, history store and web server
dashboard.html          dashboard page
map.html                live map page
settings.html           settings and station checks page
static/                 the pages' JavaScript (theme, dashboard, map, settings)
vendor/                 bundled Leaflet and topojson-client, with licences
geo/                    bundled Natural Earth coastline data, with licence
settings.example.ini    every setting, documented; copied to settings.ini
install.sh, uninstall.sh, systemd/   the service
tools/fake_readsb.py    simulated receiver for development
tests/                  unit tests and fixtures
```
</details>

## Credits and licence

The live map uses [Leaflet](https://leafletjs.com). Its offline outline is
[Natural Earth](https://www.naturalearthdata.com) data, packaged by
[world-atlas](https://github.com/topojson/world-atlas) and read with
[topojson-client](https://github.com/topojson/topojson-client). Street maps
are © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors,
and © [CARTO](https://carto.com/attributions) when you use CARTO's. The
dashboard reads what [readsb](https://github.com/wiedehopf/readsb) writes and
borrows ideas from tar1090 and graphs1090.

This project is MIT licensed; see [LICENSE](LICENSE). Bundled third-party files
keep their own licences, listed with versions and checksums in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
