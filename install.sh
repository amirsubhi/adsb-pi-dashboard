#!/usr/bin/env bash
# Installs the dashboard to ~/adsb-dashboard and runs it as a systemd
# service under the current user. Run from inside the repo:
#   ./install.sh
set -euo pipefail

INSTALL_DIR="${ADSB_DATA_DIR:-$HOME/adsb-dashboard}"
SERVICE_NAME="adsb-dashboard.service"
PYTHON_BIN="$(command -v python3)"

if [ -z "$PYTHON_BIN" ]; then
  echo "python3 not found - install it first." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
cp "$(dirname "$0")/app.py" "$(dirname "$0")/dashboard.html" "$INSTALL_DIR/"

sed \
  -e "s/__USER__/$(whoami)/g" \
  -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
  -e "s|__PYTHON__|$PYTHON_BIN|g" \
  "$(dirname "$0")/systemd/adsb-dashboard.service.template" | sudo tee "/etc/systemd/system/$SERVICE_NAME" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

sleep 1
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PORT="${ADSB_DASHBOARD_PORT:-8099}"
echo
echo "Installed and started. Dashboard: http://${IP:-<this host's IP>}:${PORT}/"
echo "Logs: journalctl -u $SERVICE_NAME -f"
