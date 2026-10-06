#!/usr/bin/env python3
"""ADS-B ground station dashboard: live status + flight history, stdlib only.

Reads the local JSON status files that readsb / dump1090-fa style decoders
write to disk, plus whatever feeder tools (fr24feed, adsbexchange-feed /
adsbexchange-mlat) are installed, and serves a single-page dashboard with a
SQLite-backed flight history log. No external Python packages required.

Configuration is via environment variables (all optional):
  ADSB_DASHBOARD_PORT   port to listen on (default 8099)
  ADSB_READSB_DIR       directory with readsb's aircraft.json/stats.json
                         (default /run/readsb)
  ADSB_ADSBX_DIR        directory with adsbexchange-feed's status.json
                         (default /run/adsbexchange-feed)
  ADSB_POLL_INTERVAL    seconds between samples (default 15)
  ADSB_SESSION_GAP      seconds of absence before a new flight session
                         starts for the same aircraft (default 300)
  ADSB_RETAIN_DAYS      how long history/metrics are kept (default 30)
  ADSB_DATA_DIR         where the SQLite database and HTML are read from
                         (default ~/adsb-dashboard)
"""
import json, os, re, shutil, sqlite3, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.environ.get("ADSB_DATA_DIR") or os.path.join(os.path.expanduser("~"), "adsb-dashboard")
DB_PATH = os.path.join(BASE, "history.sqlite")
HTML_PATH = os.path.join(BASE, "dashboard.html")
PORT = int(os.environ.get("ADSB_DASHBOARD_PORT", 8099))
READSB_DIR = os.environ.get("ADSB_READSB_DIR", "/run/readsb")
ADSBX_DIR = os.environ.get("ADSB_ADSBX_DIR", "/run/adsbexchange-feed")
POLL_INTERVAL = float(os.environ.get("ADSB_POLL_INTERVAL", 15))
SESSION_GAP = float(os.environ.get("ADSB_SESSION_GAP", 300))
RETAIN_DAYS = float(os.environ.get("ADSB_RETAIN_DAYS", 30))

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

def undervoltage_last_event():
    """Timestamp of the most recent kernel under-voltage warning, if any.
    Silently returns None on systems without journalctl or without this
    ever happening."""
    try:
        out = subprocess.run(
            ["journalctl", "-k", "-g", "Undervoltage detected", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
        if not out:
            return None
        m = re.match(r"^(\S+)", out[-1])
        return m.group(1) if m else None
    except Exception:
        return None

# ---------- fr24feed (optional) ----------

def parse_fr24():
    """Parses `fr24feed-status`. Returns {"running": False} if fr24feed
    isn't installed - the dashboard hides the FlightRadar24 card in that
    case."""
    try:
        out = subprocess.run(["fr24feed-status"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {"running": False}
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

# ---------- readsb / adsbexchange-feed local JSON (optional) ----------

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def parse_readsb_stats():
    d = read_json(os.path.join(READSB_DIR, "stats.json")) or {}
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
    recv = read_json(os.path.join(ADSBX_DIR, "receiver.json")) or read_json(os.path.join(READSB_DIR, "receiver.json"))
    if recv and recv.get("lat") is not None:
        stats["lat"] = recv.get("lat")
        stats["lon"] = recv.get("lon")
    return stats

def parse_adsbx_status():
    d = read_json(os.path.join(ADSBX_DIR, "status.json")) or {}
    return {
        "aircraft_with_pos": d.get("aircraft_with_pos"),
        "aircraft_without_pos": d.get("aircraft_without_pos"),
    }

def systemctl_active(unit):
    try:
        return subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return "unknown"

def mlat_from_journal():
    try:
        out = subprocess.run(
            ["journalctl", "-u", "adsbexchange-mlat", "-n", "30", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=5,
        ).stdout.splitlines()
    except Exception:
        out = []
    d = {}
    for line in out:
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

def read_local_aircraft():
    """The full aircraft list from readsb's own aircraft.json - this is the
    receiver's raw view, independent of which aggregators are fed from it."""
    d = read_json(os.path.join(READSB_DIR, "aircraft.json")) or {}
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

def get_conn():
    conn = sqlite3.connect(DB_PATH)
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
    return conn

def upsert_sessions(conn, aircraft, now):
    """A 'session' is one continuous sighting of an aircraft. If it's been
    gone longer than SESSION_GAP, the next sighting starts a new row -
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
        if row and (now - row[1]) <= SESSION_GAP:
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
    cutoff = now - RETAIN_DAYS * 86400
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
        time.sleep(POLL_INTERVAL)

# ---------- HTTP server ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/":
            try:
                with open(HTML_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(500)
                self.end_headers()
        elif parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif parsed.path == "/api/status":
            with LOCK:
                self._json(dict(LAST))
        elif parsed.path == "/api/history":
            hours = float(qs.get("hours", ["24"])[0])
            cutoff = time.time() - hours * 3600
            conn = get_conn()
            rows = conn.execute(
                "SELECT hex, flight, first_seen, last_seen, max_alt, max_gs, samples FROM sessions "
                "WHERE last_seen >= ? ORDER BY first_seen DESC LIMIT 400",
                (cutoff,),
            ).fetchall()
            conn.close()
            self._json([
                {"hex": r[0], "flight": r[1], "first_seen": r[2], "last_seen": r[3],
                 "max_alt": r[4], "max_gs": r[5], "samples": r[6]}
                for r in rows
            ])
        elif parsed.path == "/api/metrics":
            hours = float(qs.get("hours", ["6"])[0])
            cutoff = time.time() - hours * 3600
            conn = get_conn()
            rows = conn.execute(
                "SELECT ts, temp_c, aircraft_count FROM metrics WHERE ts >= ? ORDER BY ts ASC",
                (cutoff,),
            ).fetchall()
            conn.close()
            self._json([{"ts": r[0], "temp_c": r[1], "aircraft_count": r[2]} for r in rows])
        else:
            self.send_response(404)
            self.end_headers()

def main():
    os.makedirs(BASE, exist_ok=True)
    collect_once()
    threading.Thread(target=collector_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"adsb-dashboard listening on :{PORT}, data dir {BASE}")
    server.serve_forever()

if __name__ == "__main__":
    main()
