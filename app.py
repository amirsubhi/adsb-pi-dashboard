#!/usr/bin/env python3
"""ADS-B ground station dashboard: live status + flight history, stdlib only.

Reads the local JSON status files that readsb / dump1090-fa style decoders
write to disk, plus whatever feeder tools (fr24feed, adsbexchange-feed /
adsbexchange-mlat) are installed, and serves a single-page dashboard with a
SQLite-backed flight history log. No external Python packages required.

Settings live in settings.ini inside the data directory (see SETTINGS below
for every option). An environment variable with the same meaning overrides
the file, so older installs configured through systemd Environment= lines
keep working. The data directory itself is chosen with ADSB_DATA_DIR
(default ~/adsb-dashboard), since that is where settings.ini is read from.

The dashboard is meant for a home LAN: it has no login, so never port-forward
it to the internet. Use a VPN such as Tailscale or WireGuard for remote access.
"""
import configparser, json, math, os, re, shutil, sqlite3, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.2.0"

# ---------- settings ----------

# (key, environment variable, default, type, description). The key is the
# option name in the [dashboard] section of settings.ini.
SETTINGS = [
    ("port", "ADSB_DASHBOARD_PORT", 8099, int,
     "Port the dashboard listens on."),
    ("bind", "ADSB_BIND", "0.0.0.0", str,
     "Network address to listen on. 0.0.0.0 means every interface on your LAN; 127.0.0.1 means this Pi only."),
    ("readsb_dir", "ADSB_READSB_DIR", "/run/readsb", str,
     "Folder where readsb or dump1090 writes aircraft.json and stats.json."),
    ("adsbx_dir", "ADSB_ADSBX_DIR", "/run/adsbexchange-feed", str,
     "Folder where adsbexchange-feed writes status.json and receiver.json."),
    ("poll_interval", "ADSB_POLL_INTERVAL", 15.0, float,
     "Seconds between samples."),
    ("session_gap", "ADSB_SESSION_GAP", 300.0, float,
     "Seconds an aircraft can be out of range before its next sighting counts as a new history entry."),
    ("retain_days", "ADSB_RETAIN_DAYS", 30.0, float,
     "Days of flight history and metrics to keep."),
    ("receiver_lat", "ADSB_LAT", None, float,
     "Receiver latitude in decimal degrees. Only needed if readsb does not report it."),
    ("receiver_lon", "ADSB_LON", None, float,
     "Receiver longitude in decimal degrees. Only needed if readsb does not report it."),
    ("transition_alt", "ADSB_TRANSITION_ALT", 18000, int,
     "Altitude in feet above which altitudes are shown as flight levels. Malaysia 11000, UK 6000, USA 18000."),
    ("show_exact_location", "ADSB_SHOW_EXACT_LOCATION", False, bool,
     "Show the receiver's exact coordinates on the page. Off rounds them to about 1 km, so screenshots don't reveal your address."),
    ("cors_origin", "ADSB_CORS_ORIGIN", "", str,
     "One other website allowed to read this API, for example http://192.168.1.20:3000. Empty means none."),
]

DATA_DIR = os.environ.get("ADSB_DATA_DIR") or os.path.join(os.path.expanduser("~"), "adsb-dashboard")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.ini")
CFG = {}            # key -> value, filled by configure()
CFG_SOURCE = {}     # key -> "settings.ini" | "environment" | "default"
CFG_ERRORS = []     # human-readable problems found while loading settings

def _parse(kind, raw):
    raw = raw.strip()
    if kind is bool:
        if raw.lower() in ("1", "true", "yes", "on"):
            return True
        if raw.lower() in ("0", "false", "no", "off"):
            return False
        raise ValueError("expected yes or no")
    value = kind(raw)
    if kind is float and not math.isfinite(value):
        raise ValueError("not a finite number")
    return value

def load_settings(path=None, environ=None):
    """Resolve every setting from environment, then settings.ini, then the
    default. Returns (values, sources, errors); an invalid value falls back
    to the next source and is reported instead of crashing the service."""
    path = path or SETTINGS_PATH
    environ = os.environ if environ is None else environ
    parser = configparser.ConfigParser(interpolation=None)
    errors = []
    try:
        parser.read(path)
    except configparser.Error as e:
        errors.append("settings.ini could not be read: %s" % e)
    section = parser["dashboard"] if parser.has_section("dashboard") else {}
    known = {s[0] for s in SETTINGS}
    for key in section:
        if key not in known:
            errors.append("settings.ini: unknown option '%s' was ignored" % key)
    values, sources = {}, {}
    for key, env, default, kind, _ in SETTINGS:
        values[key], sources[key] = default, "default"
        for source, raw in (("environment", environ.get(env)), ("settings.ini", section.get(key))):
            if raw is None or raw.strip() == "":   # blank means "not set here"
                continue
            try:
                values[key], sources[key] = _parse(kind, raw), source
                break
            except (TypeError, ValueError) as e:
                where = env if source == "environment" else "settings.ini " + key
                errors.append("%s = %r is not valid (%s) and was ignored" % (where, raw, e))
    lat, lon = values["receiver_lat"], values["receiver_lon"]
    if (lat is None) != (lon is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180)):
        errors.append("receiver_lat / receiver_lon must both be set and in range; ignoring them")
        values["receiver_lat"] = values["receiver_lon"] = None
    return values, sources, errors

def configure(path=None, environ=None):
    values, sources, errors = load_settings(path, environ)
    CFG.clear(); CFG.update(values)
    CFG_SOURCE.clear(); CFG_SOURCE.update(sources)
    CFG_ERRORS[:] = errors

DB_PATH = os.path.join(DATA_DIR, "history.sqlite")
HTML_PATH = os.path.join(DATA_DIR, "dashboard.html")
SETTINGS_HTML_PATH = os.path.join(DATA_DIR, "settings.html")
STATIC_DIR = os.path.join(DATA_DIR, "static")
# The only files served besides the pages. A fixed list, so no request path
# can reach anything else on the Pi.
STATIC_FILES = {
    "/static/theme.js": "application/javascript",
    "/static/dashboard.js": "application/javascript",
    "/static/settings.js": "application/javascript",
}

LAST = {"updated": 0}
LOCK = threading.Lock()

# ---------- system vitals ----------

def read_temp():
    """CPU temperature in °C. Tries vcgencmd (Raspberry Pi) first, falls
    back to the generic thermal zone so this also runs on non-Pi boards."""
    try:
        out = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"temp=([\d.]+)", out)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None

def read_throttled():
    """Raspberry Pi firmware throttling flags (vcgencmd get_throttled).
    Returns an empty dict on non-Pi hardware."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"0x([0-9a-fA-F]+)", out)
        if not m:
            return {}
        v = int(m.group(1), 16)
        return {
            "raw": hex(v),
            "undervoltage_now": bool(v & 0x1),
            "freq_capped_now": bool(v & 0x2),
            "throttled_now": bool(v & 0x4),
            "soft_temp_now": bool(v & 0x8),
            "undervoltage_occurred": bool(v & 0x10000),
            "freq_capped_occurred": bool(v & 0x20000),
            "throttled_occurred": bool(v & 0x40000),
            "soft_temp_occurred": bool(v & 0x80000),
        }
    except Exception:
        return {}

def read_uptime_seconds():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return None

def read_meminfo():
    try:
        vals = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                vals[k] = int(rest.strip().split()[0])  # kB
        total = vals.get("MemTotal", 0) / 1024.0
        avail = vals.get("MemAvailable", 0) / 1024.0
        return {"total_mb": round(total, 0), "available_mb": round(avail, 0)}
    except Exception:
        return {}

def read_disk():
    try:
        u = shutil.disk_usage("/")
        return {"total_gb": round(u.total / 1e9, 1), "used_gb": round(u.used / 1e9, 1)}
    except Exception:
        return {}

def parse_undervoltage_lines(lines):
    """Timestamp of the last kernel under-voltage line. journalctl prints
    '-- No entries --' (and other '--' notes) when there is nothing to show,
    which must not be mistaken for a timestamp."""
    lines = [l for l in lines if l.strip() and not l.startswith("--")]
    if not lines:
        return None
    m = re.match(r"^(\S+)", lines[-1])
    return m.group(1) if m else None

_UV_CACHE = {"at": 0.0, "value": None}
UV_CHECK_EVERY = 300  # searching the kernel log is slow on an SD card; once every 5 minutes is plenty

def undervoltage_last_event():
    """Most recent kernel under-voltage warning, if any. Silently returns
    None on systems without journalctl."""
    now = time.monotonic()
    if _UV_CACHE["at"] and now - _UV_CACHE["at"] < UV_CHECK_EVERY:
        return _UV_CACHE["value"]
    try:
        out = subprocess.run(
            ["journalctl", "-k", "-g", "Undervoltage detected", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=5,
        ).stdout.splitlines()
        value = parse_undervoltage_lines(out)
    except Exception:
        value = None
    _UV_CACHE.update(at=now, value=value)
    return value

# ---------- fr24feed (optional) ----------

def parse_fr24_text(out):
    d = {"running": "running" in out}
    m = re.search(r"FR24 Link:\s*(\w+)\s*\[(\w+)\]", out)
    if m:
        d["link_status"] = m.group(1)
        d["link_type"] = m.group(2)
    m = re.search(r"FR24 Radar:\s*(\S+)", out)
    if m:
        d["radar_id"] = m.group(1).rstrip(".")
    m = re.search(r"FR24 Tracked AC:\s*(\d+)", out)
    if m:
        d["tracked_ac"] = int(m.group(1))
    m = re.search(r"Receiver:\s*(\w+)\s*\((\d+)\s*MSGS/(\d+)\s*SYNC\)", out)
    if m:
        d["receiver_status"] = m.group(1)
        d["msgs"] = int(m.group(2))
        d["sync"] = int(m.group(3))
    return d

def parse_fr24():
    """Parses `fr24feed-status`. Returns {"running": False} if fr24feed
    isn't installed - the dashboard hides the FlightRadar24 card in that
    case."""
    try:
        out = subprocess.run(["fr24feed-status"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {"running": False}
    return parse_fr24_text(out)

# ---------- readsb / adsbexchange-feed local JSON (optional) ----------

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def receiver_location():
    """(lat, lon) from receiver.json, falling back to the settings file."""
    recv = read_json(os.path.join(CFG["adsbx_dir"], "receiver.json")) or read_json(os.path.join(CFG["readsb_dir"], "receiver.json"))
    if recv and recv.get("lat") is not None and recv.get("lon") is not None:
        return recv["lat"], recv["lon"]
    if CFG["receiver_lat"] is not None:
        return CFG["receiver_lat"], CFG["receiver_lon"]
    return None

def parse_readsb_stats():
    d = read_json(os.path.join(CFG["readsb_dir"], "stats.json")) or {}
    last1 = d.get("last1min", {})
    local = last1.get("local", {})
    stats = {
        "gain_db": d.get("gain_db"),
        "estimated_ppm": d.get("estimated_ppm"),
        "signal": local.get("signal"),
        "noise": local.get("noise"),
        "messages_valid_1min": last1.get("messages_valid"),
        "positions_1min": last1.get("position_count_total"),
    }
    loc = receiver_location()
    if loc:
        stats["lat"], stats["lon"] = loc
    return stats

def parse_adsbx_status():
    d = read_json(os.path.join(CFG["adsbx_dir"], "status.json")) or {}
    return {
        "aircraft_with_pos": d.get("aircraft_with_pos"),
        "aircraft_without_pos": d.get("aircraft_without_pos"),
    }

def service_state(load_state, active_state):
    """'unknown' when the unit isn't installed (or systemd can't be asked),
    otherwise systemd's active state. is-active alone reports a missing unit
    as 'inactive', which made the dashboard show "Down" for a feeder that was
    simply never installed."""
    if load_state in ("", "not-found"):
        return "unknown"
    return active_state or "unknown"

def systemctl_active(unit):
    try:
        load = subprocess.run(["systemctl", "show", "-p", "LoadState", "--value", unit],
                              capture_output=True, text=True, timeout=3).stdout.strip()
        active = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3).stdout.strip()
        return service_state(load, active)
    except Exception:
        return "unknown"

def parse_mlat_lines(lines):
    d = {}
    for line in lines:
        m = re.search(r"Receiver:\s*(\w+)\s+([\d.]+)\s*msg/s received\s+([\d.]+)\s*msg/s processed", line)
        if m:
            d["receiver_status"] = m.group(1)
            d["msg_rate_received"] = float(m.group(2))
            d["msg_rate_processed"] = float(m.group(3))
        m = re.search(r"Results:\s*([\d.]+)\s*positions/minute", line)
        if m:
            d["positions_per_min"] = float(m.group(1))
        m = re.search(r"peer_count:\s*(\d+)", line)
        if m:
            d["peer_count"] = int(m.group(1))
    return d

def mlat_from_journal():
    try:
        out = subprocess.run(
            ["journalctl", "-u", "adsbexchange-mlat", "-n", "30", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=5,
        ).stdout.splitlines()
    except Exception:
        out = []
    return parse_mlat_lines(out)

def read_local_aircraft():
    """The full aircraft list from readsb's own aircraft.json - this is the
    receiver's raw view, independent of which aggregators are fed from it."""
    d = read_json(os.path.join(CFG["readsb_dir"], "aircraft.json")) or {}
    out = []
    for a in d.get("aircraft", []):
        flight = (a.get("flight") or "").strip() or None
        fresh_pos = a.get("lat") is not None and a.get("lon") is not None and (a.get("seen_pos") or 0) <= POSITION_MAX_AGE
        out.append({
            "hex": a.get("hex"),
            "flight": flight,
            "alt_baro": a.get("alt_baro") if isinstance(a.get("alt_baro"), (int, float)) else None,
            "gs": a.get("gs"),
            "track": a.get("track"),
            "lat": a.get("lat") if fresh_pos else None,
            "lon": a.get("lon") if fresh_pos else None,
        })
    return out, d.get("messages")

# ---------- geometry ----------

POSITION_MAX_AGE = 60      # seconds; older positions are not plotted or counted for range
MAX_PLAUSIBLE_NM = 450     # beyond line of sight for any ground receiver; treat as a bad position
SECTORS = 36               # coverage is tracked in 10-degree directions

def distance_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a)) / 1852.0

def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def positions_from(home, aircraft):
    """[(aircraft, distance_nm, bearing)] for aircraft with a usable position."""
    out = []
    if not home:
        return out
    for a in aircraft:
        if a.get("lat") is None:
            continue
        nm = distance_nm(home[0], home[1], a["lat"], a["lon"])
        if nm <= MAX_PLAUSIBLE_NM:
            out.append((a, nm, bearing_deg(home[0], home[1], a["lat"], a["lon"])))
    return out

def local_day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))

def local_midnight(ts):
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

# ---------- history store ----------

SCHEMA_VERSION = 2

def init_db():
    """Create or upgrade the database. PRAGMA user_version records which
    migrations have run, so an existing history.sqlite is upgraded in place
    instead of breaking when a release adds columns or tables."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        # WAL lets the web server read while the collector writes, instead of
        # one of them hitting "database is locked".
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            # IF NOT EXISTS: databases created before versioning already have these.
            conn.execute("""CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hex TEXT NOT NULL,
                flight TEXT,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                max_alt INTEGER,
                max_gs REAL,
                samples INTEGER DEFAULT 1
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS metrics(
                ts REAL NOT NULL,
                temp_c REAL,
                aircraft_count INTEGER,
                load1 REAL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_hex ON sessions(hex, last_seen)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts)")
            conn.execute("PRAGMA user_version = 1")
        if version < 2:
            # 1.2: message rate and furthest position per sample, coverage by
            # direction per day, and each day's furthest aircraft.
            conn.execute("ALTER TABLE metrics ADD COLUMN msg_rate REAL")
            conn.execute("ALTER TABLE metrics ADD COLUMN max_range_nm REAL")
            conn.execute("""CREATE TABLE coverage(
                day TEXT NOT NULL,
                sector INTEGER NOT NULL,
                max_nm REAL NOT NULL,
                PRIMARY KEY (day, sector)
            )""")
            conn.execute("""CREATE TABLE daily(
                day TEXT PRIMARY KEY,
                max_range_nm REAL,
                bearing REAL,
                hex TEXT,
                flight TEXT
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_last ON sessions(last_seen)")
            conn.execute("PRAGMA user_version = 2")
        conn.commit()
    finally:
        conn.close()

def get_conn():
    return sqlite3.connect(DB_PATH, timeout=10)

def upsert_sessions(conn, aircraft, now):
    """A 'session' is one continuous sighting of an aircraft. If it's been
    gone longer than session_gap, the next sighting starts a new row -
    so the same tail number passing twice shows as two history entries."""
    for a in aircraft:
        hx = a.get("hex")
        if not hx:
            continue
        row = conn.execute(
            "SELECT id, last_seen, flight, max_alt, max_gs FROM sessions WHERE hex=? ORDER BY id DESC LIMIT 1",
            (hx,),
        ).fetchone()
        alt, gs, flight = a.get("alt_baro"), a.get("gs"), a.get("flight")
        if row and (now - row[1]) <= CFG["session_gap"]:
            new_flight = flight or row[2]
            new_alt = max(filter(lambda x: x is not None, [alt, row[3]]), default=None)
            new_gs = max(filter(lambda x: x is not None, [gs, row[4]]), default=None)
            conn.execute(
                "UPDATE sessions SET last_seen=?, flight=?, max_alt=?, max_gs=?, samples=samples+1 WHERE id=?",
                (now, new_flight, new_alt, new_gs, row[0]),
            )
        else:
            conn.execute(
                "INSERT INTO sessions(hex, flight, first_seen, last_seen, max_alt, max_gs, samples) VALUES (?,?,?,?,?,?,1)",
                (hx, flight, now, now, alt, gs),
            )

def insert_metric(conn, now, temp, count, load1, msg_rate=None, max_range_nm=None):
    conn.execute("INSERT INTO metrics(ts, temp_c, aircraft_count, load1, msg_rate, max_range_nm) VALUES (?,?,?,?,?,?)",
                 (now, temp, count, load1, msg_rate, max_range_nm))

def update_coverage(conn, now, positions):
    """Keep the furthest distance heard in each 10-degree direction today,
    and today's single furthest aircraft."""
    day = local_day(now)
    best = {}
    for _, nm, brg in positions:
        sector = int(brg // (360 / SECTORS)) % SECTORS
        best[sector] = max(nm, best.get(sector, 0))
    for sector, nm in best.items():
        conn.execute("INSERT OR IGNORE INTO coverage(day, sector, max_nm) VALUES (?,?,?)", (day, sector, nm))
        conn.execute("UPDATE coverage SET max_nm = MAX(max_nm, ?) WHERE day=? AND sector=?", (nm, day, sector))
    if positions:
        a, nm, brg = max(positions, key=lambda p: p[1])
        conn.execute("INSERT OR IGNORE INTO daily(day) VALUES (?)", (day,))
        conn.execute("UPDATE daily SET max_range_nm=?, bearing=?, hex=?, flight=? "
                     "WHERE day=? AND (max_range_nm IS NULL OR max_range_nm < ?)",
                     (nm, brg, a.get("hex"), a.get("flight"), day, nm))

def today_summary(conn, now):
    """Unique aircraft since local midnight, and today's furthest aircraft."""
    unique = conn.execute("SELECT COUNT(DISTINCT hex) FROM sessions WHERE last_seen >= ?", (local_midnight(now),)).fetchone()[0]
    row = conn.execute("SELECT max_range_nm, bearing, hex, flight FROM daily WHERE day=?", (local_day(now),)).fetchone()
    furthest = None
    if row and row[0] is not None:
        furthest = {"nm": round(row[0], 1), "bearing": round(row[1]), "hex": row[2], "flight": row[3]}
    return unique, furthest

def prune(conn, now):
    cutoff = now - CFG["retain_days"] * 86400
    conn.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))
    conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM coverage WHERE day < ?", (local_day(cutoff),))
    conn.execute("DELETE FROM daily WHERE day < ?", (local_day(cutoff),))

# ---------- collector loop ----------

def collect_once():
    now = time.time()
    temp = read_temp()
    load1, load5, load15 = os.getloadavg()
    aircraft, messages_total = read_local_aircraft()
    receiver = parse_readsb_stats()
    home = (receiver["lat"], receiver["lon"]) if receiver.get("lat") is not None else None
    positions = positions_from(home, aircraft)
    valid_1min = receiver.get("messages_valid_1min")
    msg_rate = round(valid_1min / 60.0, 1) if isinstance(valid_1min, (int, float)) else None

    snapshot = {
        "updated": now,
        "version": VERSION,
        "station": {
            "transition_alt": CFG["transition_alt"],
            "show_exact_location": CFG["show_exact_location"],
        },
        "temp_c": temp,
        "throttled": read_throttled(),
        "undervoltage_last_event": undervoltage_last_event(),
        "uptime_s": read_uptime_seconds(),
        "load": {"l1": load1, "l5": load5, "l15": load15},
        "mem": read_meminfo(),
        "disk": read_disk(),
        "fr24": parse_fr24(),
        "adsbx_status": parse_adsbx_status(),
        "adsbx_feed_active": systemctl_active("adsbexchange-feed"),
        "adsbx_mlat_active": systemctl_active("adsbexchange-mlat"),
        "mlat": mlat_from_journal(),
        "receiver": receiver,
        "aircraft": aircraft,
        "messages_total": messages_total,
        "message_rate": msg_rate,
        "unique_today": None,
        "range_today": None,
    }
    with LOCK:
        LAST.clear()
        LAST.update(snapshot)

    conn = get_conn()
    try:
        upsert_sessions(conn, aircraft, now)
        max_range = round(max(p[1] for p in positions), 1) if positions else None
        insert_metric(conn, now, temp, len(aircraft), load1, msg_rate, max_range)
        update_coverage(conn, now, positions)
        prune(conn, now)
        conn.commit()
        unique, furthest = today_summary(conn, now)
    finally:
        conn.close()
    with LOCK:
        LAST.update(unique_today=unique, range_today=furthest)

def collector_loop():
    while True:
        try:
            collect_once()
        except Exception as e:
            with LOCK:
                LAST["error"] = str(e)
        time.sleep(CFG["poll_interval"])

# ---------- chart data ----------

TYPICAL_BUCKETS = 96   # 15-minute slots across the day
TYPICAL_DAYS = 7
_TYPICAL_CACHE = {"at": 0.0, "day": None, "value": None}

def typical_ranges(conn, now):
    """For each 15-minute slot of the day, the lowest and highest average
    aircraft count seen in that slot over the previous 7 days (today
    excluded, as in graphs1090). A slot needs at least 2 days of data,
    otherwise it is None and the dashboard draws no band there."""
    midnight = local_midnight(now)
    rows = conn.execute(
        "SELECT ts, aircraft_count FROM metrics WHERE ts >= ? AND ts < ? AND aircraft_count IS NOT NULL",
        (midnight - TYPICAL_DAYS * 86400, midnight),
    ).fetchall()
    sums = {}
    for ts, count in rows:
        lt = time.localtime(ts)
        key = ((lt.tm_year, lt.tm_yday), (lt.tm_hour * 60 + lt.tm_min) * TYPICAL_BUCKETS // 1440)
        s = sums.setdefault(key, [0, 0])
        s[0] += count
        s[1] += 1
    per_slot = {}
    for (day, slot), (total, n) in sums.items():
        per_slot.setdefault(slot, []).append(total / n)
    ranges = []
    for slot in range(TYPICAL_BUCKETS):
        avgs = per_slot.get(slot, [])
        ranges.append([round(min(avgs), 1), round(max(avgs), 1)] if len(avgs) >= 2 else None)
    return {"buckets": TYPICAL_BUCKETS, "days": len({day for day, _ in sums}), "ranges": ranges}

def typical_cached(now):
    # Scans a week of samples, so recompute at most every 10 minutes (and at midnight).
    c = _TYPICAL_CACHE
    if c["value"] is None or now - c["at"] > 600 or c["day"] != local_day(now):
        conn = get_conn()
        try:
            c.update(value=typical_ranges(conn, now), at=now, day=local_day(now))
        finally:
            conn.close()
    return c["value"]

def coverage_data(conn, now):
    """Furthest distance per 10-degree direction: today, and best of the last 7 days."""
    today, best = [None] * SECTORS, [None] * SECTORS
    for sector, nm in conn.execute("SELECT sector, max_nm FROM coverage WHERE day=?", (local_day(now),)):
        today[sector] = round(nm, 1)
    for sector, nm in conn.execute("SELECT sector, MAX(max_nm) FROM coverage WHERE day >= ? GROUP BY sector",
                                   (local_day(now - 6 * 86400),)):
        best[sector] = round(nm, 1)
    return {"sectors": SECTORS, "today": today, "best": best}

# ---------- settings page data ----------

def _age(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return "%d s ago" % seconds
    if seconds < 5400:
        return "%d min ago" % round(seconds / 60)
    return "%d h ago" % round(seconds / 3600)

def _timezone():
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    try:
        with open("/etc/timezone") as f:
            return f.read().strip() or None
    except Exception:
        return None

def station_checks():
    """Plain-language checks shown on the settings page. state is ok, warn or info."""
    checks = []
    def add(name, state, detail):
        checks.append({"name": name, "state": state, "detail": detail})

    path = os.path.join(CFG["readsb_dir"], "aircraft.json")
    try:
        age = time.time() - os.path.getmtime(path)
        add("Receiver data", "ok" if age < 30 else "warn",
            "aircraft.json updated %s" % _age(age) + ("" if age < 30 else ". Is readsb running?"))
    except OSError:
        add("Receiver data", "warn", "No aircraft.json in %s. Check readsb_dir." % CFG["readsb_dir"])

    loc = receiver_location()
    add("Receiver location", "ok" if loc else "warn",
        "Known" if loc else "Not reported by readsb. Set receiver_lat and receiver_lon.")

    add("FlightRadar24", "ok" if shutil.which("fr24feed-status") else "info",
        "fr24feed found" if shutil.which("fr24feed-status") else "fr24feed not installed. That's fine if you don't feed FlightRadar24.")
    add("ADSBExchange", "ok" if os.path.exists(os.path.join(CFG["adsbx_dir"], "status.json")) else "info",
        "Feed status found" if os.path.exists(os.path.join(CFG["adsbx_dir"], "status.json"))
        else "No status.json in %s. That's fine if you don't feed ADSBExchange." % CFG["adsbx_dir"])

    if shutil.which("vcgencmd"):
        ok = bool(read_throttled())
        add("Pi firmware tools", "ok" if ok else "warn",
            "vcgencmd works" if ok else "vcgencmd found but not readable. Add this user to the video group.")
    else:
        add("Pi firmware tools", "info", "vcgencmd not found, so power and throttling checks are off. Normal on non-Pi boards.")

    if shutil.which("journalctl"):
        try:
            r = subprocess.run(["journalctl", "-n", "1", "-q", "--no-pager"], capture_output=True, text=True, timeout=5)
            denied = "insufficient permissions" in (r.stderr or "") or "not seeing messages" in (r.stderr or "")
        except Exception:
            denied = True
        add("System journal", "warn" if denied else "ok",
            "Can't read other services' logs, so MLAT stats stay empty. Add this user to the systemd-journal group."
            if denied else "Readable, so MLAT and under-voltage history are available")

    tz = _timezone()
    add("Time zone", "info", tz or "Unknown")

    try:
        free = shutil.disk_usage(DATA_DIR).free / 1e9
        add("Disk space", "ok" if free > 1 else "warn", "%.1f GB free" % free)
    except OSError:
        pass
    try:
        size = os.path.getsize(DB_PATH) / 1e6
        add("History database", "ok", "%.1f MB" % size)
    except OSError:
        add("History database", "info", "Not created yet")
    return checks

def settings_payload():
    rows = []
    for key, env, default, kind, desc in SETTINGS:
        rows.append({
            "key": key, "env": env, "value": CFG[key], "default": default,
            "source": CFG_SOURCE[key], "type": kind.__name__, "description": desc,
        })
    return {
        "version": VERSION,
        "python": sys.version.split()[0],
        "data_dir": DATA_DIR,
        "settings_file": SETTINGS_PATH,
        "settings_file_exists": os.path.exists(SETTINGS_PATH),
        "settings": rows,
        "errors": list(CFG_ERRORS),
        "checks": station_checks(),
    }

# ---------- HTTP server ----------

# Scripts only from this server's /static files. Styles stay 'unsafe-inline'
# because the pages carry their CSS inline and set a few style attributes.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

class BadRequest(Exception):
    pass

def hours_param(qs, default):
    """?hours= as a positive number, capped at the retention window."""
    raw = qs.get("hours", [default])[0]
    try:
        hours = float(raw)
    except ValueError:
        raise BadRequest("hours must be a number")
    if not math.isfinite(hours) or hours <= 0:
        raise BadRequest("hours must be greater than 0")
    return min(hours, CFG["retain_days"] * 24)

def step_param(qs):
    """?step= seconds to average samples over (0 = raw samples)."""
    raw = qs.get("step", ["0"])[0]
    try:
        step = int(raw)
    except ValueError:
        raise BadRequest("step must be a whole number of seconds")
    if step != 0 and not 10 <= step <= 3600:
        raise BadRequest("step must be 0 or between 10 and 3600")
    return step

class Handler(BaseHTTPRequestHandler):
    server_version = "adsb-dashboard/" + VERSION
    timeout = 20  # seconds; stops a stalled client from holding a thread forever

    def log_message(self, fmt, *args):
        pass

    def _common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        if CFG["cors_origin"]:
            self.send_header("Access-Control-Allow-Origin", CFG["cors_origin"])
            self.send_header("Vary", "Origin")

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _page(self, path):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._json({"error": "page file missing: %s" % os.path.basename(path)}, 500)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path, ctype):
        try:
            with open(os.path.join(STATIC_DIR, os.path.basename(path)), "rb") as f:
                body = f.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._route()
        except BadRequest as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            print("error handling %s: %r" % (self.path, e), file=sys.stderr)
            self._json({"error": "internal error"}, 500)

    def _route(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/":
            self._page(HTML_PATH)
        elif parsed.path == "/settings":
            self._page(SETTINGS_HTML_PATH)
        elif parsed.path in STATIC_FILES:
            self._static(parsed.path, STATIC_FILES[parsed.path])
        elif parsed.path == "/favicon.ico":
            self.send_response(204)
            self._common_headers()
            self.end_headers()
        elif parsed.path == "/api/status":
            with LOCK:
                snapshot = dict(LAST)
            self._json(snapshot)
        elif parsed.path == "/api/settings":
            self._json(settings_payload())
        elif parsed.path == "/api/history":
            cutoff = time.time() - hours_param(qs, "24") * 3600
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT hex, flight, first_seen, last_seen, max_alt, max_gs, samples FROM sessions "
                    "WHERE last_seen >= ? ORDER BY first_seen DESC LIMIT 400",
                    (cutoff,),
                ).fetchall()
            finally:
                conn.close()
            self._json([
                {"hex": r[0], "flight": r[1], "first_seen": r[2], "last_seen": r[3],
                 "max_alt": r[4], "max_gs": r[5], "samples": r[6]}
                for r in rows
            ])
        elif parsed.path == "/api/metrics":
            cutoff = time.time() - hours_param(qs, "6") * 3600
            step = step_param(qs)
            conn = get_conn()
            try:
                if step:
                    # Averaged buckets keep a day of data to a few hundred rows.
                    rows = conn.execute(
                        "SELECT MAX(ts), AVG(temp_c), ROUND(AVG(aircraft_count), 1), AVG(msg_rate), MAX(max_range_nm) "
                        "FROM metrics WHERE ts >= ? GROUP BY CAST(ts / ? AS INTEGER) ORDER BY 1 ASC",
                        (cutoff, step),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT ts, temp_c, aircraft_count, msg_rate, max_range_nm FROM metrics WHERE ts >= ? ORDER BY ts ASC",
                        (cutoff,),
                    ).fetchall()
            finally:
                conn.close()
            self._json([{"ts": r[0], "temp_c": r[1], "aircraft_count": r[2], "msg_rate": r[3], "max_range_nm": r[4]}
                        for r in rows])
        elif parsed.path == "/api/typical":
            self._json(typical_cached(time.time()))
        elif parsed.path == "/api/coverage":
            conn = get_conn()
            try:
                data = coverage_data(conn, time.time())
            finally:
                conn.close()
            self._json(data)
        else:
            self._json({"error": "not found"}, 404)

def main():
    configure()
    for problem in CFG_ERRORS:
        print("settings: " + problem, file=sys.stderr)
    os.makedirs(DATA_DIR, exist_ok=True)
    init_db()
    collect_once()
    threading.Thread(target=collector_loop, daemon=True).start()
    server = ThreadingHTTPServer((CFG["bind"], CFG["port"]), Handler)
    print("adsb-dashboard %s listening on %s:%d, data dir %s" % (VERSION, CFG["bind"], CFG["port"], DATA_DIR))
    server.serve_forever()

if __name__ == "__main__":
    main()
