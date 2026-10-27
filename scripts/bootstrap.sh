#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env. Edit it, add your VPN configuration, then run this script again."
  exit 0
fi

env_value() {
  local key="$1" fallback="$2" value
  value="$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' .env)"
  value="${value%\"}"
  value="${value#\"}"
  printf '%s' "${value:-$fallback}"
}

MEDIA_ROOT="$(env_value MEDIA_ROOT /srv/media)"
CONFIG_ROOT="$(env_value CONFIG_ROOT /srv/jellypi/config)"
PUID="$(env_value PUID "$(id -u)")"
PGID="$(env_value PGID "$(id -g)")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install docker.io and docker-compose-v2, then rerun this script." >&2
  exit 1
fi

sudo mkdir -p \
  "$MEDIA_ROOT/Movies" \
  "$MEDIA_ROOT/TV" \
  "$MEDIA_ROOT/Audiobooks" \
  "$MEDIA_ROOT/Downloads/complete" \
  "$MEDIA_ROOT/Downloads/incomplete" \
  "$MEDIA_ROOT/usenet/complete/movies" \
  "$MEDIA_ROOT/usenet/complete/tv" \
  "$MEDIA_ROOT/usenet/complete/audiobooks" \
  "$MEDIA_ROOT/usenet/incomplete" \
  "$MEDIA_ROOT/.jellypi-encoder" \
  "$CONFIG_ROOT"/{jellyfin,seerr,radarr,sonarr,prowlarr,bazarr,qbittorrent,gluetun,sabnzbd,download-recovery} \
  "$CONFIG_ROOT/chaptarr" \
  "$CONFIG_ROOT/audiobookrequest" \
  "$CONFIG_ROOT/audiobookshelf/config" \
  "$CONFIG_ROOT/audiobookshelf/metadata"

sudo chown -R "$PUID:$PGID" "$MEDIA_ROOT" "$CONFIG_ROOT"
sudo chmod -R u+rwX,g+rwX "$MEDIA_ROOT" "$CONFIG_ROOT"

if [[ ! -f "$CONFIG_ROOT/gluetun/custom.conf" ]]; then
  echo "Place your provider's OpenVPN file at $CONFIG_ROOT/gluetun/custom.conf before starting the stack." >&2
  exit 1
fi

docker compose config >/dev/null
docker compose up -d --build
echo "JellyPi is starting. Open http://$(hostname -I | awk '{print $1}'):8096 after the containers become healthy."
