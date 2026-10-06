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

VERSION = "1.1.0"

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
        out.append({
            "hex": a.get("hex"),
            "flight": flight,
            "alt_baro": a.get("alt_baro") if isinstance(a.get("alt_baro"), (int, float)) else None,
            "gs": a.get("gs"),
            "track": a.get("track"),
        })
    return out, d.get("messages")

# ---------- history store ----------

SCHEMA_VERSION = 1

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

def insert_metric(conn, now, temp, count, load1):
    conn.execute("INSERT INTO metrics(ts, temp_c, aircraft_count, load1) VALUES (?,?,?,?)", (now, temp, count, load1))

def prune(conn, now):
    cutoff = now - CFG["retain_days"] * 86400
    conn.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))
    conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))

# ---------- collector loop ----------

def collect_once():
    now = time.time()
    temp = read_temp()
    load1, load5, load15 = os.getloadavg()
    aircraft, messages_total = read_local_aircraft()

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
        "receiver": parse_readsb_stats(),
        "aircraft": aircraft,
        "messages_total": messages_total,
    }
    with LOCK:
        LAST.clear()
        LAST.update(snapshot)

    conn = get_conn()
    try:
        upsert_sessions(conn, aircraft, now)
        insert_metric(conn, now, temp, len(aircraft), load1)
        prune(conn, now)
        conn.commit()
    finally:
        conn.close()

def collector_loop():
    while True:
        try:
            collect_once()
        except Exception as e:
            with LOCK:
                LAST["error"] = str(e)
        time.sleep(CFG["poll_interval"])

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

# Pages still carry inline <script>/<style>, hence 'unsafe-inline'. Everything
# else is locked to this server; nothing is loaded from other sites.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
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
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT ts, temp_c, aircraft_count FROM metrics WHERE ts >= ? ORDER BY ts ASC",
                    (cutoff,),
                ).fetchall()
            finally:
                conn.close()
            self._json([{"ts": r[0], "temp_c": r[1], "aircraft_count": r[2]} for r in rows])
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
