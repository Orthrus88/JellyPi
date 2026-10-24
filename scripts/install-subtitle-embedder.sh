#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_SCRIPT=/usr/local/sbin/jellypi-embed-clean-subs.py
CONFIG_FILE=/etc/default/jellypi-subtitle-embed

if [[ ! -f "$SOURCE_DIR/.env" ]]; then
  echo "Run scripts/bootstrap.sh and configure .env before installing the subtitle embedder." >&2
  exit 1
fi

read_env() {
  local key="$1" fallback="$2" value
  value="$(sed -n "s/^${key}=//p" "$SOURCE_DIR/.env" | tail -n 1)"
  printf '%s' "${value:-$fallback}"
}

media_root="$(read_env MEDIA_ROOT /srv/media)"
config_root="$(read_env CONFIG_ROOT /srv/jellypi/config)"

sudo install -D -m 0755 "$SOURCE_DIR/scripts/embed-clean-subs.py" "$TARGET_SCRIPT"
sudo tee "$CONFIG_FILE" >/dev/null <<EOF
MEDIA_ROOT=$media_root
JELLYFIN_DB=$config_root/jellyfin/data/jellyfin.db
JELLYFIN_URL=http://127.0.0.1:8096
EOF
sudo chmod 0644 "$CONFIG_FILE"
sudo cp "$SOURCE_DIR/systemd/jellypi-subtitle-embed.service" /etc/systemd/system/
sudo cp "$SOURCE_DIR/systemd/jellypi-subtitle-embed.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jellypi-subtitle-embed.timer
sudo systemctl start --no-block jellypi-subtitle-embed.service
systemctl status jellypi-subtitle-embed.timer --no-pager
