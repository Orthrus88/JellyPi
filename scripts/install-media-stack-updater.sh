#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$SOURCE_DIR/.env" ]]; then
  echo "Run scripts/bootstrap.sh and configure .env first." >&2
  exit 1
fi

MEDIA_ROOT="$(awk -F= '$1 == "MEDIA_ROOT" {sub(/^[^=]*=/, ""); print; exit}' "$SOURCE_DIR/.env")"
MEDIA_ROOT="${MEDIA_ROOT%\"}"
MEDIA_ROOT="${MEDIA_ROOT#\"}"
MEDIA_ROOT="${MEDIA_ROOT:-/srv/media}"

sudo install -m 0755 "$SOURCE_DIR/scripts/media-stack-stable-update.sh" \
  /usr/local/sbin/jellypi-media-stack-update
sudo install -m 0644 "$SOURCE_DIR/systemd/jellypi-media-stack-update.service" \
  /etc/systemd/system/jellypi-media-stack-update.service
sudo install -m 0644 "$SOURCE_DIR/systemd/jellypi-media-stack-update.timer" \
  /etc/systemd/system/jellypi-media-stack-update.timer
sudo tee /etc/default/jellypi-media-stack-update >/dev/null <<EOF
JELLYPI_STACK_DIR=$SOURCE_DIR
EOF
sudo install -d -m 0755 /etc/systemd/system/jellypi-media-stack-update.service.d
sudo tee /etc/systemd/system/jellypi-media-stack-update.service.d/storage.conf >/dev/null <<EOF
[Unit]
RequiresMountsFor=$MEDIA_ROOT
ConditionPathExists=$SOURCE_DIR/compose.yaml
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now jellypi-media-stack-update.timer
systemctl status jellypi-media-stack-update.timer --no-pager
