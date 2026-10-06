#!/usr/bin/env bash
# Stops and removes the systemd service. Leaves the data directory
# (history.sqlite, dashboard.html) in place - delete it yourself if you
# want the flight history gone too:
#   rm -rf ~/adsb-dashboard
set -euo pipefail

SERVICE_NAME="adsb-dashboard.service"
sudo systemctl disable --now "$SERVICE_NAME" || true
sudo rm -f "/etc/systemd/system/$SERVICE_NAME"
sudo systemctl daemon-reload
echo "Service removed. Settings and flight history are still in ${ADSB_DATA_DIR:-$HOME/adsb-dashboard}."
