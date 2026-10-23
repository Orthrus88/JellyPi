#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_SCRIPT=/usr/local/libexec/jellypi-vpn-watchdog
PROJECT_FILE=/etc/jellypi-vpn-watchdog.project

if [[ ! -f "$SOURCE_DIR/.env" ]]; then
  echo "Run scripts/bootstrap.sh and configure .env before installing the watchdog." >&2
  exit 1
fi

sudo install -D -m 0755 "$SOURCE_DIR/scripts/vpn-watchdog.sh" "$TARGET_SCRIPT"
printf '%s\n' "$SOURCE_DIR" | sudo tee "$PROJECT_FILE" >/dev/null
sudo chmod 644 "$PROJECT_FILE"
sudo cp "$SOURCE_DIR/systemd/jellypi-vpn-watchdog.service" /etc/systemd/system/
sudo cp "$SOURCE_DIR/systemd/jellypi-vpn-watchdog.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jellypi-vpn-watchdog.timer
systemctl status jellypi-vpn-watchdog.timer --no-pager
