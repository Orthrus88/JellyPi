#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$SOURCE_DIR/.env" ]]; then
  echo "Run scripts/bootstrap.sh and configure .env first." >&2
  exit 1
fi

CONFIG_ROOT="$(awk -F= '$1 == "CONFIG_ROOT" {sub(/^[^=]*=/, ""); print; exit}' "$SOURCE_DIR/.env")"
CONFIG_ROOT="${CONFIG_ROOT%\"}"
CONFIG_ROOT="${CONFIG_ROOT#\"}"
CONFIG_ROOT="${CONFIG_ROOT:-/srv/jellypi/config}"
MEDIA_ROOT="$(awk -F= '$1 == "MEDIA_ROOT" {sub(/^[^=]*=/, ""); print; exit}' "$SOURCE_DIR/.env")"
MEDIA_ROOT="${MEDIA_ROOT%\"}"
MEDIA_ROOT="${MEDIA_ROOT#\"}"
MEDIA_ROOT="${MEDIA_ROOT:-/srv/media}"

sudo install -m 0755 "$SOURCE_DIR/recovery/missing_search.py" \
  /usr/local/sbin/jellypi-missing-search.py
sudo install -m 0644 "$SOURCE_DIR/systemd/jellypi-missing-search.service" \
  /etc/systemd/system/jellypi-missing-search.service
sudo install -m 0644 "$SOURCE_DIR/systemd/jellypi-missing-search.timer" \
  /etc/systemd/system/jellypi-missing-search.timer
sudo tee /etc/default/jellypi-missing-search >/dev/null <<EOF
SONARR_CONFIG=$CONFIG_ROOT/sonarr/config.xml
RADARR_CONFIG=$CONFIG_ROOT/radarr/config.xml
MISSING_SEARCH_STATE=$CONFIG_ROOT/missing-search/state.json
EOF
sudo install -d -m 0755 /etc/systemd/system/jellypi-missing-search.service.d
sudo tee /etc/systemd/system/jellypi-missing-search.service.d/storage.conf >/dev/null <<EOF
[Unit]
RequiresMountsFor=$MEDIA_ROOT
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now jellypi-missing-search.timer
systemctl status jellypi-missing-search.timer --no-pager
