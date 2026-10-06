"""Tests for app.py. Standard library only; run from the repo root with:

    python3 -m unittest discover -s tests -v
"""
import http.client, json, os, shutil, sqlite3, sys, tempfile, threading, time, unittest
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
        self.saved = (app.DATA_DIR, app.DB_PATH, app.SETTINGS_PATH, app.HTML_PATH, app.SETTINGS_HTML_PATH, app.MAP_HTML_PATH)
        app.DATA_DIR = self.tmp
        app.DB_PATH = os.path.join(self.tmp, "history.sqlite")
        app.SETTINGS_PATH = os.path.join(self.tmp, "settings.ini")
        app.HTML_PATH = os.path.join(self.tmp, "dashboard.html")
        app.SETTINGS_HTML_PATH = os.path.join(self.tmp, "settings.html")
        app.MAP_HTML_PATH = os.path.join(self.tmp, "map.html")
        self.configure()

    def configure(self, ini="", env=None):
        with open(app.SETTINGS_PATH, "w") as f:
            f.write(ini)
        environ = {"ADSB_READSB_DIR": os.path.join(FIXTURES, "readsb"), "ADSB_ADSBX_DIR": os.path.join(self.tmp, "no-adsbx")}
        environ.update(env or {})
        app.configure(app.SETTINGS_PATH, environ)

    def tearDown(self):
        app.DATA_DIR, app.DB_PATH, app.SETTINGS_PATH, app.HTML_PATH, app.SETTINGS_HTML_PATH, app.MAP_HTML_PATH = self.saved
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
                             "tracked_ac": 38, "receiver_status": "connected", "msgs": 1243881, "sync": 21,
                             "mlat_status": "ok", "mlat_ac": 30})

    def test_fr24_not_running(self):
        self.assertEqual(app.parse_fr24_text(""), {"running": False})
        # "not running" contains "running"; this used to show a stopped feeder as up
        self.assertEqual(app.parse_fr24_text(fixture("fr24feed-status-stopped.txt")), {"running": False})

    def test_fr24_faults_are_parsed_not_dropped(self):
        d = app.parse_fr24_text(fixture("fr24feed-status-faults.txt"))
        self.assertTrue(d["running"])
        self.assertEqual(d["link_status"], "connecting")
        self.assertNotIn("link_type", d)
        self.assertEqual(d["receiver_status"], "down")
        self.assertEqual(d["mlat_status"], "not running")

    def test_fr24_without_status_markers(self):
        d = app.parse_fr24_text(fixture("fr24feed-status-plain.txt"))
        self.assertEqual((d["running"], d["link_status"], d["link_type"], d["receiver_status"]), (True, "connected", "UDP", "connected"))
        self.assertNotIn("msgs", d)

    def test_mlat_server_status(self):
        self.assertEqual(app.parse_mlat_lines(fixture("mlat-journal.txt").splitlines())["server_status"], "connected")

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

class HealthTest(TempDataDir):
    """Feeder states and the debounced alerts built from a snapshot."""
    def snap(self, **over):
        d = {"uptime_s": 86400, "temp_c": 52.0, "throttled": {}, "disk": {"total_gb": 30.0, "used_gb": 8.0, "free_gb": 20.5},
             "fr24": dict(app.parse_fr24_text(fixture("fr24feed-status.txt")), installed=True),
             "adsbx_feed_active": "active", "adsbx_mlat_active": "active",
             "mlat": app.parse_mlat_lines(fixture("mlat-journal.txt").splitlines()), "receiver_age_s": 0.4}
        d.update(over)
        d["feeders"] = app.feeder_states(d)
        return d

    def states(self, **over):
        return {k: v["state"] for k, v in self.snap(**over)["feeders"].items()}

    def test_all_well(self):
        s = self.snap()
        self.assertEqual(self.states(), {"fr24": "ok", "adsbx": "ok", "receiver": "ok"})
        self.assertEqual(app.health_alerts(s, {}, 1000.0), [])

    def test_feeder_states(self):
        faults = dict(app.parse_fr24_text(fixture("fr24feed-status-faults.txt")), installed=True)
        self.assertEqual(self.states(fr24=faults)["fr24"], "down")
        self.assertEqual(self.states(fr24={"installed": True, "running": False})["fr24"], "stopped")
        self.assertEqual(self.states(fr24={"installed": False, "running": False})["fr24"], "absent")
        self.assertEqual(self.states(fr24=dict(self.snap()["fr24"], mlat_status="not running"))["fr24"], "degraded")
        self.assertEqual(self.states(adsbx_feed_active="unknown")["adsbx"], "absent")
        self.assertEqual(self.states(adsbx_feed_active="inactive")["adsbx"], "stopped")
        self.assertEqual(self.states(adsbx_feed_active="failed")["adsbx"], "down")
        self.assertEqual(self.states(adsbx_mlat_active="failed")["adsbx"], "degraded")
        self.assertEqual(self.states(mlat={})["adsbx"], "degraded")  # nothing logged in the last 30 minutes
        self.assertEqual(self.states(adsbx_mlat_active="unknown", mlat={})["adsbx"], "ok")  # MLAT not installed
        self.assertEqual(self.states(receiver_age_s=95)["receiver"], "down")
        self.assertEqual(self.states(receiver_age_s=None)["receiver"], "absent")

    def test_feeder_alerts_wait_before_firing(self):
        s, since = self.snap(adsbx_feed_active="activating"), {}
        self.assertEqual(app.health_alerts(s, since, 1000.0), [])
        self.assertEqual(app.health_alerts(s, since, 1080.0), [])
        alerts = app.health_alerts(s, since, 1091.0)
        self.assertEqual([(a["id"], a["level"], a["since"]) for a in alerts], [("adsbx", "warning", 1000.0)])
        # once it recovers the clock resets, so the next blip waits again
        app.health_alerts(self.snap(), since, 1100.0)
        self.assertEqual(app.health_alerts(s, since, 1110.0), [])

    def test_fr24_link_loss_is_a_warning(self):
        fr = dict(self.snap()["fr24"], link_status="connecting")
        s, since = self.snap(fr24=fr), {}
        app.health_alerts(s, since, 0.0)
        self.assertEqual(app.health_alerts(s, since, 121.0)[0]["text"], "FlightRadar24 feed is down: no link to FlightRadar24 (connecting).")

    def test_mlat_problems_are_a_slow_caution(self):
        s, since = self.snap(adsbx_mlat_active="failed"), {}
        app.health_alerts(s, since, 0.0)
        self.assertEqual(app.health_alerts(s, since, 599.0), [])
        self.assertEqual([(a["id"], a["level"]) for a in app.health_alerts(s, since, 600.0)], [("adsbx-mlat", "caution")])

    def test_alert_wording(self):
        fr = dict(self.snap()["fr24"], mlat_status="not running")
        s, since = self.snap(fr24=fr, adsbx_feed_active="inactive"), {}
        app.health_alerts(s, since, 0.0)
        self.assertEqual([a["text"] for a in app.health_alerts(s, since, 600.0)],
                         ["ADSBExchange feed is down: adsbexchange-feed is stopped.", "FlightRadar24 MLAT isn't working: not running."])

    def test_boot_grace_holds_feeders_but_not_power(self):
        s, since = self.snap(uptime_s=120, adsbx_feed_active="activating", throttled={"undervoltage_now": True}), {}
        app.health_alerts(s, since, 0.0)
        self.assertEqual([a["id"] for a in app.health_alerts(s, since, 200.0)], ["power"])

    def test_stale_receiver(self):
        s = self.snap(receiver_age_s=75)
        self.assertEqual([a["id"] for a in app.health_alerts(s, {}, 0.0)], ["receiver"])

    def test_warnings_come_before_cautions(self):
        s = self.snap(temp_c=77.0, throttled={"undervoltage_now": True}, disk={"total_gb": 30.0, "used_gb": 29.5, "free_gb": 0.5})
        self.assertEqual([(a["id"], a["level"]) for a in app.health_alerts(s, {}, 0.0)],
                         [("power", "warning"), ("temp", "caution"), ("disk", "caution")])

    def test_disk_nearly_full(self):
        ids = lambda **d: [a["id"] for a in app.health_alerts(self.snap(disk=d), {}, 0.0)]
        self.assertEqual(ids(total_gb=64.0, used_gb=59.0, free_gb=5.0), ["disk"])   # over 90 % used
        self.assertEqual(ids(total_gb=8.0, used_gb=6.5, free_gb=0.8), ["disk"])     # under 1 GB free
        self.assertEqual(ids(total_gb=30.0, used_gb=20.0, free_gb=10.0), [])

    def test_receiver_age_from_aircraft_json(self):
        self.assertEqual(app.receiver_age(1791274012.5), 12.5)
        self.configure(env={"ADSB_READSB_DIR": self.tmp})
        self.assertIsNone(app.receiver_age(1791274012.5))

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

    def test_version_1_database_is_upgraded_in_place(self):
        conn = sqlite3.connect(app.DB_PATH)
        conn.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, hex TEXT NOT NULL, flight TEXT, "
                     "first_seen REAL NOT NULL, last_seen REAL NOT NULL, max_alt INTEGER, max_gs REAL, samples INTEGER DEFAULT 1)")
        conn.execute("CREATE TABLE metrics(ts REAL NOT NULL, temp_c REAL, aircraft_count INTEGER, load1 REAL)")
        conn.execute("INSERT INTO metrics VALUES (100, 50.5, 7, 0.2)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit(); conn.close()
        app.init_db()
        conn = app.get_conn()
        self.assertEqual(conn.execute("SELECT ts, temp_c, aircraft_count, msg_rate, max_range_nm FROM metrics").fetchall(),
                         [(100, 50.5, 7, None, None)])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0], 0)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
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

# ---------- geometry and daily statistics ----------

class GeometryTest(unittest.TestCase):
    def test_distance_and_bearing(self):
        self.assertAlmostEqual(app.distance_nm(0, 0, 1, 0), 60.04, places=1)  # one degree of latitude
        self.assertAlmostEqual(app.bearing_deg(0, 0, 1, 0), 0)
        self.assertAlmostEqual(app.bearing_deg(0, 0, 0, 1), 90)
        self.assertAlmostEqual(app.bearing_deg(0, 0, -1, 0), 180)
        self.assertAlmostEqual(app.bearing_deg(0, 0, 0, -1), 270)

    def test_positions_skip_missing_and_implausible(self):
        home = (0.0, 0.0)
        aircraft = [{"hex": "a", "lat": 1.0, "lon": 0.0}, {"hex": "b", "lat": None, "lon": None},
                    {"hex": "c", "lat": 0.0, "lon": 20.0}]  # ~1200 nm away
        got = app.positions_from(home, aircraft)
        self.assertEqual([p[0]["hex"] for p in got], ["a"])
        self.assertEqual(app.positions_from(None, aircraft), [])

class ReadsbPositionTest(TempDataDir):
    def test_stale_position_is_dropped(self):
        os.makedirs(os.path.join(self.tmp, "rb"))
        with open(os.path.join(self.tmp, "rb", "aircraft.json"), "w") as f:
            json.dump({"aircraft": [{"hex": "a", "lat": 1, "lon": 2, "seen_pos": 5},
                                    {"hex": "b", "lat": 1, "lon": 2, "seen_pos": 300}]}, f)
        self.configure(env={"ADSB_READSB_DIR": os.path.join(self.tmp, "rb")})
        aircraft, _ = app.read_local_aircraft()
        self.assertEqual([(a["hex"], a["lat"]) for a in aircraft], [("a", 1), ("b", None)])

class DailyStatsTest(TempDataDir):
    def setUp(self):
        super().setUp()
        app.init_db()
        self.conn = app.get_conn()
        self.now = time.mktime((2026, 10, 6, 14, 0, 0, 0, 0, -1))  # 14:00 local

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def test_coverage_keeps_the_furthest_per_direction(self):
        north = {"hex": "n1", "flight": "MAS1"}
        app.update_coverage(self.conn, self.now, [(north, 120.0, 5.0), ({"hex": "e1", "flight": None}, 80.0, 95.0)])
        app.update_coverage(self.conn, self.now, [(north, 100.0, 6.0), ({"hex": "n2", "flight": "AXM2"}, 150.0, 9.9)])
        cov = app.coverage_data(self.conn, self.now)
        self.assertEqual(len(cov["today"]), 36)
        self.assertEqual(cov["today"][0], 150.0)
        self.assertEqual(cov["today"][9], 80.0)
        self.assertIsNone(cov["today"][18])
        self.assertEqual(cov["best"], cov["today"])

    def test_best_includes_earlier_days(self):
        app.update_coverage(self.conn, self.now - 2 * 86400, [({"hex": "x"}, 200.0, 185.0)])
        app.update_coverage(self.conn, self.now - 10 * 86400, [({"hex": "y"}, 300.0, 185.0)])  # older than 7 days
        cov = app.coverage_data(self.conn, self.now)
        self.assertIsNone(cov["today"][18])
        self.assertEqual(cov["best"][18], 200.0)

    def test_today_summary(self):
        app.upsert_sessions(self.conn, [{"hex": "a"}, {"hex": "b"}], self.now - 60)
        app.upsert_sessions(self.conn, [{"hex": "a"}], self.now)
        app.upsert_sessions(self.conn, [{"hex": "old"}], self.now - 86400)  # yesterday
        app.update_coverage(self.conn, self.now, [({"hex": "a", "flight": "MAS1"}, 90.0, 270.4), ({"hex": "b", "flight": None}, 40.0, 10.0)])
        unique, furthest = app.today_summary(self.conn, self.now)
        self.assertEqual(unique, 2)
        self.assertEqual(furthest, {"nm": 90.0, "bearing": 270, "hex": "a", "flight": "MAS1"})

    def test_no_furthest_without_positions(self):
        self.assertEqual(app.today_summary(self.conn, self.now), (0, None))

    def test_typical_ranges_need_two_days(self):
        midnight = app.local_midnight(self.now)
        slot = 40  # 10:00-10:15
        for days_ago, counts in ((1, [10, 12]), (2, [20, 22]), (3, [30, 30])):
            for i, c in enumerate(counts):
                app.insert_metric(self.conn, midnight - days_ago * 86400 + slot * 900 + i * 60, 50, c, 0.1)
        app.insert_metric(self.conn, midnight - 86400 + 41 * 900, 50, 5, 0.1)  # only one day in slot 41
        app.insert_metric(self.conn, midnight + slot * 900, 50, 99, 0.1)  # today: excluded
        t = app.typical_ranges(self.conn, self.now)
        self.assertEqual(t["buckets"], 96)
        self.assertEqual(t["days"], 3)
        self.assertEqual(t["ranges"][slot], [11.0, 30.0])
        self.assertIsNone(t["ranges"][41])
        self.assertIsNone(t["ranges"][0])

    def test_prune_drops_old_coverage(self):
        app.update_coverage(self.conn, self.now - 40 * 86400, [({"hex": "x"}, 100.0, 0.0)])
        app.update_coverage(self.conn, self.now, [({"hex": "y"}, 100.0, 0.0)])
        app.prune(self.conn, self.now)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0], 1)

# ---------- live map ----------

class MapSettingsTest(TempDataDir):
    def test_default_is_openstreetmap(self):
        self.assertEqual(app.CFG["map_tiles"], "osm")
        self.assertEqual(app.map_config()["tiles"]["provider"], "osm")

    def test_carto_with_key(self):
        self.configure("[dashboard]\nmap_tiles = CARTO\ncarto_key = Ab-12_x\n")
        tiles = app.map_config()["tiles"]
        self.assertEqual(tiles["provider"], "carto")
        self.assertTrue(tiles["url"].endswith("?key=Ab-12_x"))
        self.assertIn("{theme}_all", tiles["url"])
        self.assertIn("CARTO", tiles["attribution"])

    def test_carto_without_key_falls_back_to_osm(self):
        self.configure("[dashboard]\nmap_tiles = carto\n")
        self.assertEqual(app.CFG["map_tiles"], "osm")
        self.assertIn("carto_key", app.CFG_ERRORS[0])

    def test_key_with_odd_characters_rejected(self):
        self.configure("[dashboard]\nmap_tiles = carto\ncarto_key = abc\"><script>\n")
        self.assertEqual(app.CFG["carto_key"], "")
        self.assertEqual(app.CFG["map_tiles"], "osm")

    def test_unknown_provider(self):
        self.configure("[dashboard]\nmap_tiles = google\n")
        self.assertEqual(app.CFG["map_tiles"], "osm")
        self.assertTrue(app.CFG_ERRORS)

    def test_off_means_no_tiles(self):
        self.configure("[dashboard]\nmap_tiles = off\n")
        self.assertIsNone(app.map_config()["tiles"])

class LiveAircraftTest(TempDataDir):
    def write(self, aircraft, now=1000.0):
        folder = os.path.join(self.tmp, "rb")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "aircraft.json")
        with open(path, "w") as f:
            json.dump({"now": now, "aircraft": aircraft}, f)
        os.utime(path, (now, now))
        self.configure(env={"ADSB_READSB_DIR": folder})

    def test_fields(self):
        self.write([{"hex": "750606", "flight": "MAS1  ", "r": "9M-MAG", "t": "A359", "lat": 3.0, "lon": 101.5, "seen_pos": 1.2,
                     "alt_baro": "ground", "gs": 12.5, "track": 90, "geom_rate": -64, "squawk": "7700", "rssi": -9.5},
                    {"hex": "75aaaa", "lat": 3.0, "lon": 101.5, "seen_pos": 200, "alt_baro": 30000, "baro_rate": 128},
                    {"flight": "NOHEX"}])
        a, b = app.live_aircraft()["aircraft"]
        self.assertEqual((a["flight"], a["reg"], a["type"], a["alt"], a["vr"], a["squawk"]), ("MAS1", "9M-MAG", "A359", "ground", -64, "7700"))
        self.assertEqual((a["lat"], a["lon"]), (3.0, 101.5))
        self.assertIsNone(b["lat"])   # position too old to plot
        self.assertEqual((b["alt"], b["vr"]), (30000, 128))

    def test_missing_file(self):
        self.configure(env={"ADSB_READSB_DIR": os.path.join(self.tmp, "nowhere")})
        self.assertEqual(app.live_aircraft()["aircraft"], [])

    def test_cache_follows_file_changes(self):
        self.write([{"hex": "a"}], now=1000)
        self.assertEqual(len(app.live_aircraft()["aircraft"]), 1)
        self.write([{"hex": "a"}, {"hex": "b"}], now=1001)
        self.assertEqual(len(app.live_aircraft()["aircraft"]), 2)

class TrailsTest(unittest.TestCase):
    def test_trails_grow_skip_duplicates_and_expire(self):
        trails = {}
        a = {"hex": "a", "lat": 1.0, "lon": 2.0, "alt": 1000}
        app.update_trails(trails, [a, {"hex": "nopos", "lat": None}], 100)
        app.update_trails(trails, [a], 102)                          # hasn't moved
        app.update_trails(trails, [dict(a, lat=1.01, alt="ground")], 104)
        self.assertEqual(trails, {"a": [[1.0, 2.0, 1000, 102], [1.01, 2.0, 0, 104]]})
        app.update_trails(trails, [], 104 + app.TRAIL_SECONDS - 1)   # first point now too old
        self.assertEqual(trails, {"a": [[1.01, 2.0, 0, 104]]})
        app.update_trails(trails, [], 104 + app.TRAIL_SECONDS + 1)   # gone for 5 minutes
        self.assertEqual(trails, {})

# ---------- query validation ----------

class StepParamTest(unittest.TestCase):
    def test_step(self):
        self.assertEqual(app.step_param({}), 0)
        self.assertEqual(app.step_param({"step": ["300"]}), 300)
        for bad in ("5", "99999", "abc", "1.5"):
            with self.assertRaises(app.BadRequest, msg=bad):
                app.step_param({"step": [bad]})

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
        for name in ("dashboard.html", "settings.html", "map.html"):
            shutil.copy(os.path.join(ROOT, name), self.tmp)
        for folder in ("static", "vendor", "geo"):
            shutil.copytree(os.path.join(ROOT, folder), os.path.join(self.tmp, folder))
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
                             ("/api/history?hours=2", 200), ("/api/metrics", 200), ("/api/metrics?hours=24&step=300", 200),
                             ("/api/metrics?step=1", 400), ("/api/typical", 200), ("/api/coverage", 200),
                             ("/static/dashboard.js", 200), ("/static/theme.js", 200), ("/static/settings.js", 200),
                             ("/static/../app.py", 404), ("/static/app.py", 404), ("/nope", 404), ("/../app.py", 404),
                             ("/map", 200), ("/api/aircraft", 200), ("/api/trails", 200), ("/api/map-config", 200),
                             ("/static/map.js", 200), ("/vendor/leaflet/leaflet.js", 200), ("/vendor/leaflet/leaflet.css", 200),
                             ("/vendor/topojson-client/topojson-client.min.js", 200), ("/geo/countries-50m.json", 200),
                             ("/vendor/leaflet/LICENSE", 404), ("/geo/../app.py", 404)]:
            self.assertEqual(self.get(path)[0], status, path)

    def test_status_contents(self):
        _, _, body = self.get("/api/status")
        d = json.loads(body)
        self.assertEqual(len(d["aircraft"]), 3)
        self.assertEqual(d["station"], {"transition_alt": 18000, "show_exact_location": False})
        self.assertEqual(d["message_rate"], round(61234 / 60.0, 1))
        self.assertEqual(d["unique_today"], 3)
        self.assertEqual(d["range_today"]["hex"], "750606")  # the only fixture aircraft with a position
        self.assertEqual(d["aircraft"][0]["lat"], 3.9)
        self.assertEqual(d["version"], app.VERSION)

    def test_history_records_sightings(self):
        _, _, body = self.get("/api/history?hours=1")
        self.assertEqual(sorted(r["hex"] for r in json.loads(body)), ["750606", "75aec8", "7c6b2d"])

    def test_bad_hours_is_a_400_not_a_dropped_connection(self):
        status, _, body = self.get("/api/history?hours=abc")
        self.assertEqual(status, 400)
        self.assertIn("hours", json.loads(body)["error"])

    def test_static_files(self):
        status, h, body = self.get("/static/dashboard.js")
        self.assertTrue(h["content-type"].startswith("application/javascript"))
        self.assertIn(b"refreshStatus", body)

    def test_scripts_only_from_this_server(self):
        _, h, _ = self.get("/")
        self.assertIn("script-src 'self';", h["content-security-policy"])
        self.assertNotIn("script-src 'self' 'unsafe-inline'", h["content-security-policy"])
        for page in ("dashboard.html", "settings.html", "map.html"):
            with open(os.path.join(ROOT, page)) as f:
                html = f.read()
            self.assertNotIn("<script>", html, page)  # inline scripts would be blocked by the CSP
            self.assertNotRegex(html, r"\son[a-z]+=", page)  # so would inline event handlers

    def test_vendored_files_are_cacheable_and_pages_are_not(self):
        _, h, _ = self.get("/vendor/leaflet/leaflet.js")
        self.assertEqual(h["cache-control"], "max-age=86400")
        self.assertTrue(h["content-type"].startswith("application/javascript"))
        self.assertEqual(self.get("/geo/countries-50m.json")[1]["content-type"], "application/json; charset=utf-8")
        self.assertEqual(self.get("/static/map.js")[1]["cache-control"], "no-cache")

    def test_tile_host_allowed_only_for_the_provider_in_use(self):
        self.assertIn("img-src 'self' data: https://tile.openstreetmap.org;", self.get("/map")[1]["content-security-policy"])
        self.configure("[dashboard]\nmap_tiles = off\n")
        self.assertIn("img-src 'self' data:;", self.get("/map")[1]["content-security-policy"])
        self.configure("[dashboard]\nmap_tiles = carto\ncarto_key = abc123\n")
        self.assertIn("https://*.basemaps.cartocdn.com", self.get("/map")[1]["content-security-policy"])

    def test_map_endpoints(self):
        d = json.loads(self.get("/api/aircraft")[2])
        self.assertEqual(len(d["aircraft"]), 3)
        cfg = json.loads(self.get("/api/map-config")[2])
        self.assertEqual(cfg["receiver"], {"lat": 2.7456, "lon": 101.7099})
        self.assertEqual(cfg["tiles"]["provider"], "osm")

    def test_carto_key_not_printed_on_settings_page(self):
        self.configure("[dashboard]\nmap_tiles = carto\ncarto_key = abc123\n")
        rows = {s["key"]: s["value"] for s in json.loads(self.get("/api/settings")[2])["settings"]}
        self.assertEqual(rows["carto_key"], "set")
        self.assertEqual(rows["map_tiles"], "carto")

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
