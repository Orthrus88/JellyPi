#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_SCRIPT=/usr/local/sbin/jellypi-normalize-subtitle-sidecars.py
CONFIG_FILE=/etc/default/jellypi-subtitle-sidecars

if [[ ! -f "$SOURCE_DIR/.env" ]]; then
  echo "Run scripts/bootstrap.sh and configure .env before installing subtitle normalization." >&2
  exit 1
fi

read_env() {
  local key="$1" fallback="$2" value
  value="$(sed -n "s/^${key}=//p" "$SOURCE_DIR/.env" | tail -n 1)"
  printf '%s' "${value:-$fallback}"
}

media_root="$(read_env MEDIA_ROOT /srv/media)"

sudo install -D -m 0755 "$SOURCE_DIR/scripts/normalize-subtitle-sidecars.py" "$TARGET_SCRIPT"
sudo tee "$CONFIG_FILE" >/dev/null <<EOF
MEDIA_ROOT=$media_root
EOF
sudo chmod 0644 "$CONFIG_FILE"
sudo cp "$SOURCE_DIR/systemd/jellypi-subtitle-sidecars.service" /etc/systemd/system/
sudo cp "$SOURCE_DIR/systemd/jellypi-subtitle-sidecars.timer" /etc/systemd/system/
sudo systemctl disable --now jellypi-subtitle-embed.timer 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl enable --now jellypi-subtitle-sidecars.timer
sudo systemctl start --no-block jellypi-subtitle-sidecars.service
systemctl status jellypi-subtitle-sidecars.timer --no-pager
