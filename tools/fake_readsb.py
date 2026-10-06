#!/usr/bin/env python3
"""Simulated readsb output for developing without a receiver.

Writes aircraft.json, stats.json and receiver.json into a folder once a
second, with aircraft that move, climb and descend like real traffic.
Point the dashboard at it:

    python3 tools/fake_readsb.py --dir /tmp/fake-readsb &
    ADSB_DATA_DIR="$PWD" ADSB_READSB_DIR=/tmp/fake-readsb python3 app.py

Standard library only, like the dashboard itself.
"""
import argparse, json, math, os, random, time

NM = 1852.0
CALLSIGNS = ["MAS", "AXM", "MXD", "FFM", "SIA", "UAE", "QTR", "CPA", "GIA", "AIQ", "THY", "KLM"]

def destination(lat, lon, bearing, metres):
    r, d, b = 6371000.0, metres / 6371000.0, math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180

def new_aircraft(home, at_edge=False):
    dist = random.uniform(150, 190) if at_edge else math.sqrt(random.random()) * 180
    lat, lon = destination(home[0], home[1], random.uniform(0, 360), dist * NM)
    alt = random.choice([random.uniform(1500, 11000), random.uniform(11000, 41000)])
    return {
        "hex": "%06x" % random.randint(0x750000, 0x75FFFF),
        "flight": (random.choice(CALLSIGNS) + str(random.randint(1, 2999))).ljust(8) if random.random() < 0.9 else None,
        "lat": lat, "lon": lon, "track": random.uniform(0, 360),
        "gs": random.uniform(220, 260) if alt < 10000 else random.uniform(380, 500),
        "alt_baro": alt, "baro_rate": random.choice([0, 0, -1280, 1600]),
        "squawk": "%04o" % random.randint(0o1000, 0o6777),
        "category": random.choice(["A3", "A3", "A5", "A2"]),
    }

def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)  # readsb also replaces files atomically

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="/tmp/fake-readsb", help="folder to write the JSON files into")
    ap.add_argument("--lat", type=float, default=2.7456, help="receiver latitude")
    ap.add_argument("--lon", type=float, default=101.7099, help="receiver longitude")
    ap.add_argument("--count", type=int, default=25, help="number of aircraft in range")
    ap.add_argument("--no-location", action="store_true", help="leave the position out of receiver.json")
    args = ap.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    home = (args.lat, args.lon)
    planes = [new_aircraft(home) for _ in range(args.count)]
    messages, last = 0, time.time()
    write_json(os.path.join(args.dir, "receiver.json"),
               {"version": "fake", "refresh": 1000, "history": 0} if args.no_location
               else {"version": "fake", "refresh": 1000, "history": 0, "lat": args.lat, "lon": args.lon})
    print("Writing simulated readsb data to %s every second. Ctrl+C to stop." % args.dir)
    while True:
        now = time.time()
        dt, last = now - last, now
        for i, p in enumerate(planes):
            p["lat"], p["lon"] = destination(p["lat"], p["lon"], p["track"], p["gs"] * NM / 3600 * dt)
            p["alt_baro"] = min(41000, max(0, p["alt_baro"] + p["baro_rate"] * dt / 60))
            if p["alt_baro"] <= 0 or p["alt_baro"] >= 41000:
                p["baro_rate"] = 0
            dlat, dlon = math.radians(p["lat"] - home[0]), math.radians(p["lon"] - home[1])
            a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(home[0])) * math.cos(math.radians(p["lat"])) * math.sin(dlon / 2) ** 2
            if 2 * 6371000 * math.asin(math.sqrt(a)) / NM > 200:
                planes[i] = new_aircraft(home, at_edge=True)
        messages += int(len(planes) * random.uniform(18, 26) * dt)
        aircraft = []
        for p in planes:
            a = {k: v for k, v in p.items() if v is not None}
            a["alt_baro"] = "ground" if p["alt_baro"] < 50 else int(round(p["alt_baro"] / 25) * 25)
            a["rssi"] = round(random.uniform(-30, -6), 1)
            a["seen"] = round(random.uniform(0, 1.5), 1)
            a["seen_pos"] = round(random.uniform(0, 2), 1)
            a["messages"] = random.randint(50, 5000)
            aircraft.append(a)
        write_json(os.path.join(args.dir, "aircraft.json"), {"now": now, "messages": messages, "aircraft": aircraft})
        write_json(os.path.join(args.dir, "stats.json"), {
            "gain_db": 43.9, "estimated_ppm": -1.2,
            "last1min": {"messages_valid": len(planes) * 1300, "position_count_total": len(planes) * 70,
                         "local": {"signal": -11.4, "noise": -34.9}},
        })
        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
