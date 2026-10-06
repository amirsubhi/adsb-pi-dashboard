#!/usr/bin/env bash
# Installs (or updates) the dashboard in ~/adsb-dashboard and runs it as a
# systemd service under the current user. Run from inside the repo:
#   ./install.sh
# Re-running it updates the program files and keeps settings.ini and the
# flight history.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${ADSB_DATA_DIR:-$HOME/adsb-dashboard}"
SERVICE_NAME="adsb-dashboard.service"
PYTHON_BIN="$(command -v python3 || true)"

if [ -z "$PYTHON_BIN" ]; then
  echo "python3 not found - install it first (sudo apt install python3)." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(sys.version_info < (3, 7))'; then
  echo "Python 3.7 or newer is needed; found $("$PYTHON_BIN" --version 2>&1)." >&2
  exit 1
fi

if [[ "$INSTALL_DIR" =~ [[:space:]] ]]; then
  echo "The data folder path can't contain spaces (systemd can't use it): $INSTALL_DIR" >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR/app.py" "$REPO_DIR/dashboard.html" "$REPO_DIR/settings.html" "$REPO_DIR/map.html" "$REPO_DIR/settings.example.ini" "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR/static"
cp "$REPO_DIR"/static/*.js "$INSTALL_DIR/static/"
# Bundled third-party files for the live map (Leaflet, coastline data) and their licences.
mkdir -p "$INSTALL_DIR/vendor" "$INSTALL_DIR/geo"
cp -R "$REPO_DIR/vendor/." "$INSTALL_DIR/vendor/"
cp -R "$REPO_DIR/geo/." "$INSTALL_DIR/geo/"

# First install: create settings.ini from the example and, when run
# interactively, ask for the two values most people need to set.
SETTINGS_FILE="$INSTALL_DIR/settings.ini"
if [ ! -f "$SETTINGS_FILE" ]; then
  cp "$REPO_DIR/settings.example.ini" "$SETTINGS_FILE"
  if [ -t 0 ]; then
    echo
    echo "Altitudes above your country's transition altitude are shown as flight levels (FL)."
    echo "Examples: Malaysia 11000, UK 6000, USA 18000."
    read -r -p "Transition altitude in feet [18000]: " TA || TA=""
    if [[ "$TA" =~ ^[0-9]{3,5}$ ]]; then
      sed -i "s/^# transition_alt = .*/transition_alt = $TA/" "$SETTINGS_FILE"
    fi
    if [ ! -f /run/readsb/receiver.json ] && [ ! -f /run/adsbexchange-feed/receiver.json ]; then
      echo
      echo "readsb isn't reporting the receiver's position. Enter it to enable distances,"
      echo "or press Enter to skip (you can add it to settings.ini later)."
      read -r -p "Receiver latitude, e.g. 2.7456: " LAT || LAT=""
      read -r -p "Receiver longitude, e.g. 101.7099: " LON || LON=""
      if [[ "$LAT" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && [[ "$LON" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
        sed -i -e "s/^# receiver_lat =.*/receiver_lat = $LAT/" -e "s/^# receiver_lon =.*/receiver_lon = $LON/" "$SETTINGS_FILE"
      fi
    fi
  fi
  echo "Created $SETTINGS_FILE"
fi

# Before 1.1, settings were Environment= lines in the service file, which is
# about to be rewritten. Carry any of them over into settings.ini so an
# upgrade doesn't silently drop them.
OLD_UNIT="/etc/systemd/system/$SERVICE_NAME"
if [ -f "$OLD_UNIT" ] && grep -q '^Environment=.*ADSB_' "$OLD_UNIT"; then
  "$PYTHON_BIN" -B - "$OLD_UNIT" "$SETTINGS_FILE" "$INSTALL_DIR" <<'PY'
import configparser, re, shlex, sys
unit, settings_file, install_dir = sys.argv[1:4]
sys.path.insert(0, install_dir)
import app
env_to_key = {env: key for key, env, *_ in app.SETTINGS}
found = {}
for line in open(unit):
    if line.startswith("Environment="):
        for item in shlex.split(line[len("Environment="):]):
            name, _, value = item.partition("=")
            if name in env_to_key:
                found[env_to_key[name]] = value
parser = configparser.ConfigParser(interpolation=None)
parser.read(settings_file)
already = parser["dashboard"] if parser.has_section("dashboard") else {}
text = open(settings_file).read()
for key, value in found.items():
    if already.get(key, "").strip():
        continue
    line = "%s = %s" % (key, value)
    text, n = re.subn(r"(?m)^# %s =.*$" % re.escape(key), lambda m: line, text, count=1)
    if not n:
        text = text.rstrip("\n") + "\n" + line + "\n"
    print("Moved %s from the old service file into settings.ini" % key)
open(settings_file, "w").write(text)
PY
fi

# Give the service the groups it needs to read Pi firmware status (video)
# and other services' logs (systemd-journal, adm) - only those that exist.
SVC_GROUPS=""
for g in video systemd-journal adm; do
  if getent group "$g" > /dev/null; then SVC_GROUPS="$SVC_GROUPS $g"; fi
done

# A non-default data folder has to reach the service too, or app.py would
# look for its files in ~/adsb-dashboard.
ENVIRONMENT=""
if [ "$INSTALL_DIR" != "$HOME/adsb-dashboard" ]; then
  ENVIRONMENT="ADSB_DATA_DIR=$INSTALL_DIR"
fi

# Render the unit with Python rather than sed so paths with special
# characters can't break the substitution.
"$PYTHON_BIN" - "$REPO_DIR/systemd/adsb-dashboard.service.template" "$(whoami)" "$INSTALL_DIR" "$PYTHON_BIN" "${SVC_GROUPS# }" "$ENVIRONMENT" <<'PY' | sudo tee "/etc/systemd/system/$SERVICE_NAME" > /dev/null
import sys
template, user, install_dir, python, groups, environment = sys.argv[1:7]
text = open(template).read()
text = text.replace("__USER__", user).replace("__INSTALL_DIR__", install_dir).replace("__PYTHON__", python)
lines = []
for line in text.splitlines():
    if line.startswith("SupplementaryGroups=__GROUPS__"):
        if groups:
            lines.append("SupplementaryGroups=" + groups)
    elif line.startswith("Environment=__ENVIRONMENT__"):
        if environment:
            lines.append('Environment="%s"' % environment)
    else:
        lines.append(line)
print("\n".join(lines))
PY

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" > /dev/null
sudo systemctl restart "$SERVICE_NAME"

sleep 1
PORT="$(ADSB_DATA_DIR="$INSTALL_DIR" "$PYTHON_BIN" -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import app; app.configure(); print(app.CFG["port"])' "$INSTALL_DIR" 2>/dev/null || echo 8099)"
HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST="${HOST:-this-pi-address}"
echo
echo "Installed and started. Dashboard: http://$HOST:$PORT/"
echo "Settings and station checks:    http://$HOST:$PORT/settings"
echo "Settings file: $SETTINGS_FILE"
echo "Logs: journalctl -u $SERVICE_NAME -f"

TZ_NAME="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
if [ "$TZ_NAME" = "Etc/UTC" ] || [ "$TZ_NAME" = "UTC" ]; then
  echo
  echo "Note: this Pi's clock is set to UTC, so times on the dashboard will be in UTC."
  echo "To use local time, run for example: sudo timedatectl set-timezone Asia/Kuala_Lumpur"
fi
echo
echo "The dashboard has no login. Keep it on your home network and don't port-forward it."
