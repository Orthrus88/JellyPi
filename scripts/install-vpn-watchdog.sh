#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR=/opt/jellypi

sudo mkdir -p "$TARGET_DIR"
sudo cp "$SOURCE_DIR/compose.yaml" "$TARGET_DIR/compose.yaml"
sudo cp -R "$SOURCE_DIR/encoder" "$SOURCE_DIR/cleanup" "$SOURCE_DIR/scripts" "$TARGET_DIR/"
sudo cp "$SOURCE_DIR/.env" "$TARGET_DIR/.env"
sudo chmod 600 "$TARGET_DIR/.env"
sudo cp "$SOURCE_DIR/systemd/jellypi-vpn-watchdog.service" /etc/systemd/system/
sudo cp "$SOURCE_DIR/systemd/jellypi-vpn-watchdog.timer" /etc/systemd/system/
sudo chmod +x "$TARGET_DIR/scripts/vpn-watchdog.sh"
sudo systemctl daemon-reload
sudo systemctl enable --now jellypi-vpn-watchdog.timer
systemctl status jellypi-vpn-watchdog.timer --no-pager
