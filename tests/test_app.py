"""Tests for app.py. Standard library only; run from the repo root with:

    python3 -m unittest discover -s tests -v
"""
import http.client, json, os, shutil, sqlite3, sys, tempfile, threading, unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, ROOT)
import app  # noqa: E402

def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()

class TempDataDir(unittest.TestCase):
    """Points app at an empty data folder and the readsb fixtures."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (app.DATA_DIR, app.DB_PATH, app.SETTINGS_PATH, app.HTML_PATH, app.SETTINGS_HTML_PATH)
        app.DATA_DIR = self.tmp
        app.DB_PATH = os.path.join(self.tmp, "history.sqlite")
        app.SETTINGS_PATH = os.path.join(self.tmp, "settings.ini")
        app.HTML_PATH = os.path.join(self.tmp, "dashboard.html")
        app.SETTINGS_HTML_PATH = os.path.join(self.tmp, "settings.html")
        self.configure()

    def configure(self, ini="", env=None):
        with open(app.SETTINGS_PATH, "w") as f:
            f.write(ini)
        environ = {"ADSB_READSB_DIR": os.path.join(FIXTURES, "readsb"), "ADSB_ADSBX_DIR": os.path.join(self.tmp, "no-adsbx")}
        environ.update(env or {})
        app.configure(app.SETTINGS_PATH, environ)

    def tearDown(self):
        app.DATA_DIR, app.DB_PATH, app.SETTINGS_PATH, app.HTML_PATH, app.SETTINGS_HTML_PATH = self.saved
        shutil.rmtree(self.tmp)

# ---------- settings ----------

class SettingsTest(TempDataDir):
    def test_defaults_when_nothing_is_set(self):
        self.configure(env={"ADSB_READSB_DIR": "", "ADSB_ADSBX_DIR": ""})
        self.assertEqual(app.CFG["port"], 8099)
        self.assertEqual(app.CFG["bind"], "0.0.0.0")
        self.assertEqual(app.CFG["readsb_dir"], "/run/readsb")
        self.assertIsNone(app.CFG["receiver_lat"])
        self.assertFalse(app.CFG["show_exact_location"])
        self.assertEqual(app.CFG["cors_origin"], "")
        self.assertEqual(app.CFG_SOURCE["port"], "default")
        self.assertEqual(app.CFG_ERRORS, [])

    def test_settings_file_values_and_types(self):
        self.configure("[dashboard]\nport = 9000\ntransition_alt = 11000\nshow_exact_location = yes\npoll_interval = 7.5\n")
        self.assertEqual(app.CFG["port"], 9000)
        self.assertEqual(app.CFG["transition_alt"], 11000)
        self.assertIs(app.CFG["show_exact_location"], True)
        self.assertEqual(app.CFG["poll_interval"], 7.5)
        self.assertEqual(app.CFG_SOURCE["port"], "settings.ini")

    def test_environment_overrides_settings_file(self):
        self.configure("[dashboard]\nport = 9000\n", {"ADSB_DASHBOARD_PORT": "9100"})
        self.assertEqual(app.CFG["port"], 9100)
        self.assertEqual(app.CFG_SOURCE["port"], "environment")

    def test_invalid_value_is_reported_and_falls_back(self):
        self.configure("[dashboard]\npoll_interval = abc\nport = 9000\n", {"ADSB_DASHBOARD_PORT": "lots"})
        self.assertEqual(app.CFG["poll_interval"], 15.0)
        self.assertEqual(app.CFG["port"], 9000)  # bad env value, so settings.ini wins
        self.assertEqual(len(app.CFG_ERRORS), 2)

    def test_blank_option_means_default(self):
        self.configure("[dashboard]\nreceiver_lat =\ncors_origin =\n")
        self.assertIsNone(app.CFG["receiver_lat"])
        self.assertEqual(app.CFG_ERRORS, [])

    def test_non_finite_numbers_rejected(self):
        self.configure("[dashboard]\nretain_days = inf\n")
        self.assertEqual(app.CFG["retain_days"], 30.0)
        self.assertTrue(app.CFG_ERRORS)

    def test_unknown_option_reported(self):
        self.configure("[dashboard]\nprot = 9000\n")
        self.assertIn("prot", app.CFG_ERRORS[0])

    def test_half_a_location_is_ignored(self):
        self.configure("[dashboard]\nreceiver_lat = 2.7\n")
        self.assertIsNone(app.CFG["receiver_lat"])
        self.assertTrue(app.CFG_ERRORS)

    def test_out_of_range_location_is_ignored(self):
        self.configure("[dashboard]\nreceiver_lat = 95\nreceiver_lon = 101\n")
        self.assertIsNone(app.CFG["receiver_lat"])

    def test_percent_sign_does_not_break_parsing(self):
        self.configure("[dashboard]\ncors_origin = http://example.lan/%41\n")
        self.assertEqual(app.CFG["cors_origin"], "http://example.lan/%41")

    def test_missing_file_uses_defaults(self):
        os.remove(app.SETTINGS_PATH)
        app.configure(app.SETTINGS_PATH, {})
        self.assertEqual(app.CFG["port"], 8099)

    def test_example_file_matches_the_settings_list(self):
        """Every option in SETTINGS is documented in settings.example.ini."""
        text = fixture(os.path.join("..", "..", "settings.example.ini"))
        for key, env, *_ in app.SETTINGS:
            self.assertIn("# %s =" % key, text)
            self.assertIn(env, text)

# ---------- parsers ----------

class ParserTest(unittest.TestCase):
    def test_fr24_status(self):
        d = app.parse_fr24_text(fixture("fr24feed-status.txt"))
        self.assertEqual(d, {"running": True, "link_status": "connected", "link_type": "TCP", "radar_id": "T-WMKK214",
                             "tracked_ac": 38, "receiver_status": "connected", "msgs": 1243881, "sync": 21})

    def test_fr24_not_running(self):
        self.assertEqual(app.parse_fr24_text(""), {"running": False})

    def test_mlat_journal_takes_latest_values(self):
        d = app.parse_mlat_lines(fixture("mlat-journal.txt").splitlines())
        self.assertEqual(d["peer_count"], 17)
        self.assertEqual(d["positions_per_min"], 12.0)
        self.assertEqual(d["msg_rate_received"], 160.2)
        self.assertEqual(d["msg_rate_processed"], 44.1)

    def test_no_undervoltage_entries(self):
        # journalctl prints "-- No entries --"; that used to come out as "--".
        self.assertIsNone(app.parse_undervoltage_lines(fixture("undervoltage-none.txt").splitlines()))
        self.assertIsNone(app.parse_undervoltage_lines([]))

    def test_last_undervoltage_event(self):
        self.assertEqual(app.parse_undervoltage_lines(fixture("undervoltage-events.txt").splitlines()), "2026-10-05T21:40:09+0800")

class ServiceStateTest(unittest.TestCase):
    def test_missing_unit_is_unknown_not_down(self):
        self.assertEqual(app.service_state("not-found", "inactive"), "unknown")
        self.assertEqual(app.service_state("", ""), "unknown")  # no systemd at all

    def test_installed_unit_reports_its_state(self):
        self.assertEqual(app.service_state("loaded", "active"), "active")
        self.assertEqual(app.service_state("loaded", "failed"), "failed")
        self.assertEqual(app.service_state("loaded", "inactive"), "inactive")

class ReadsbTest(TempDataDir):
    def test_aircraft_list(self):
        aircraft, messages = app.read_local_aircraft()
        self.assertEqual(messages, 48231907)
        self.assertEqual([a["flight"] for a in aircraft], ["MAS2668", "AXM712", None])
        self.assertIsNone(aircraft[1]["alt_baro"])  # "ground" isn't a number

    def test_receiver_location_from_readsb(self):
        self.assertEqual(app.receiver_location(), (2.7456, 101.7099))

    def test_receiver_location_falls_back_to_settings(self):
        self.configure("[dashboard]\nreceiver_lat = 1.5\nreceiver_lon = 103.5\n", {"ADSB_READSB_DIR": self.tmp})
        self.assertEqual(app.receiver_location(), (1.5, 103.5))

    def test_stats(self):
        s = app.parse_readsb_stats()
        self.assertEqual(s["messages_valid_1min"], 61234)
        self.assertEqual((s["lat"], s["lon"]), (2.7456, 101.7099))

# ---------- history store ----------

class HistoryTest(TempDataDir):
    def test_fresh_database_is_versioned_and_uses_wal(self):
        app.init_db()
        conn = app.get_conn()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], app.SCHEMA_VERSION)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        conn.close()

    def test_database_from_before_versioning_is_kept(self):
        conn = sqlite3.connect(app.DB_PATH)
        conn.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, hex TEXT NOT NULL, flight TEXT, "
                     "first_seen REAL NOT NULL, last_seen REAL NOT NULL, max_alt INTEGER, max_gs REAL, samples INTEGER DEFAULT 1)")
        conn.execute("INSERT INTO sessions(hex, flight, first_seen, last_seen) VALUES ('750606', 'MAS2668', 1, 2)")
        conn.commit(); conn.close()
        app.init_db()
        conn = app.get_conn()
        self.assertEqual(conn.execute("SELECT flight FROM sessions").fetchall(), [("MAS2668",)])
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], app.SCHEMA_VERSION)
        conn.close()

    def test_init_db_is_repeatable(self):
        app.init_db(); app.init_db()

    def test_sessions_extend_then_split_after_gap(self):
        app.init_db()
        conn = app.get_conn()
        gap = app.CFG["session_gap"]
        app.upsert_sessions(conn, [{"hex": "750606", "flight": None, "alt_baro": 12000, "gs": 300}], 1000)
        app.upsert_sessions(conn, [{"hex": "750606", "flight": "MAS2668", "alt_baro": 9000, "gs": 410}], 1000 + gap)
        app.upsert_sessions(conn, [{"hex": "750606", "flight": "MAS2668", "alt_baro": 5000, "gs": 250}], 1000 + 3 * gap)
        rows = conn.execute("SELECT flight, first_seen, last_seen, max_alt, max_gs, samples FROM sessions ORDER BY id").fetchall()
        self.assertEqual(rows, [("MAS2668", 1000, 1000 + gap, 12000, 410, 2), ("MAS2668", 1000 + 3 * gap, 1000 + 3 * gap, 5000, 250, 1)])
        conn.close()

    def test_aircraft_without_hex_is_skipped(self):
        app.init_db()
        conn = app.get_conn()
        app.upsert_sessions(conn, [{"hex": None, "flight": "X"}], 1000)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        conn.close()

    def test_prune_removes_old_rows(self):
        app.init_db()
        conn = app.get_conn()
        now = 100 * 86400
        app.insert_metric(conn, now - 31 * 86400, 50, 3, 0.1)
        app.insert_metric(conn, now - 86400, 50, 3, 0.1)
        app.prune(conn, now)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 1)
        conn.close()

# ---------- query validation ----------

class HoursParamTest(TempDataDir):
    def test_valid_and_capped(self):
        self.assertEqual(app.hours_param({}, "24"), 24.0)
        self.assertEqual(app.hours_param({"hours": ["2"]}, "24"), 2.0)
        self.assertEqual(app.hours_param({"hours": ["99999"]}, "24"), 30 * 24)

    def test_rejected(self):
        for bad in ("abc", "0", "-5", "nan", "inf", "1e999", ""):
            with self.assertRaises(app.BadRequest, msg=bad):
                app.hours_param({"hours": [bad]}, "24")

# ---------- HTTP ----------

class HttpTest(TempDataDir):
    def setUp(self):
        super().setUp()
        for name in ("dashboard.html", "settings.html"):
            shutil.copy(os.path.join(ROOT, name), self.tmp)
        app.init_db()
        app.collect_once()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, dict((k.lower(), v) for k, v in r.getheaders()), body

    def test_pages_and_api_routes(self):
        for path, status in [("/", 200), ("/settings", 200), ("/api/status", 200), ("/api/settings", 200),
                             ("/api/history?hours=2", 200), ("/api/metrics", 200), ("/nope", 404), ("/../app.py", 404)]:
            self.assertEqual(self.get(path)[0], status, path)

    def test_status_contents(self):
        _, _, body = self.get("/api/status")
        d = json.loads(body)
        self.assertEqual(len(d["aircraft"]), 3)
        self.assertEqual(d["station"], {"transition_alt": 18000, "show_exact_location": False})
        self.assertEqual(d["version"], app.VERSION)

    def test_history_records_sightings(self):
        _, _, body = self.get("/api/history?hours=1")
        self.assertEqual(sorted(r["hex"] for r in json.loads(body)), ["750606", "75aec8", "7c6b2d"])

    def test_bad_hours_is_a_400_not_a_dropped_connection(self):
        status, _, body = self.get("/api/history?hours=abc")
        self.assertEqual(status, 400)
        self.assertIn("hours", json.loads(body)["error"])

    def test_security_headers(self):
        _, h, _ = self.get("/")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", h["content-security-policy"])
        self.assertEqual(h["referrer-policy"], "no-referrer")

    def test_no_cors_header_by_default(self):
        _, h, _ = self.get("/api/status")
        self.assertNotIn("access-control-allow-origin", h)

    def test_cors_header_when_configured(self):
        self.configure("[dashboard]\ncors_origin = http://192.168.1.20:3000\n")
        _, h, _ = self.get("/api/status")
        self.assertEqual(h["access-control-allow-origin"], "http://192.168.1.20:3000")

    def test_settings_payload(self):
        _, _, body = self.get("/api/settings")
        d = json.loads(body)
        self.assertEqual([s["key"] for s in d["settings"]], [s[0] for s in app.SETTINGS])
        names = [c["name"] for c in d["checks"]]
        self.assertIn("Receiver data", names)
        self.assertIn("Receiver location", names)
        self.assertTrue(all(c["state"] in ("ok", "warn", "info") for c in d["checks"]))

    def test_missing_page_file_is_reported(self):
        os.remove(app.SETTINGS_HTML_PATH)
        self.assertEqual(self.get("/settings")[0], 500)

if __name__ == "__main__":
    unittest.main()
