#!/usr/bin/env bash
set -u

PROJECT_FILE="${JELLYPI_PROJECT_FILE:-/etc/jellypi-vpn-watchdog.project}"
if [[ ! -r "$PROJECT_FILE" ]]; then
  echo "JellyPi project location is missing: $PROJECT_FILE" >&2
  exit 1
fi
read -r PROJECT_DIR < "$PROJECT_FILE"
COMPOSE_FILE="${JELLYPI_COMPOSE_FILE:-$PROJECT_DIR/compose.yaml}"
ENV_FILE="${JELLYPI_ENV_FILE:-$PROJECT_DIR/.env}"
COMPOSE=(/usr/bin/docker compose --project-directory "$PROJECT_DIR" --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
exec 9>/run/jellypi-vpn-watchdog.lock
/usr/bin/flock -n 9 || exit 0

vpn_path_ok() {
  [[ "$(/usr/bin/docker inspect --format '{{.State.Running}}' gluetun 2>/dev/null)" == "true" ]] &&
  [[ "$(/usr/bin/docker inspect --format '{{.State.Running}}' qbittorrent 2>/dev/null)" == "true" ]] &&
  /usr/bin/docker exec qbittorrent /usr/bin/wget -q --timeout=10 --tries=1 -O /dev/null https://www.cloudflare.com/cdn-cgi/trace
}

if vpn_path_ok; then
  echo "VPN and qBittorrent network path are healthy."
  exit 0
fi

if ! /usr/bin/curl -fsS --connect-timeout 5 --max-time 10 https://www.cloudflare.com/cdn-cgi/trace >/dev/null; then
  echo "Internet connection is unavailable; leaving containers alone."
  exit 1
fi

echo "VPN path is unavailable; recreating Gluetun and qBittorrent."
"${COMPOSE[@]}" up -d --force-recreate gluetun qbittorrent

for _ in $(seq 1 24); do
  if vpn_path_ok; then
    echo "VPN and qBittorrent network path recovered."
    exit 0
  fi
  sleep 5
done

echo "The VPN path did not recover within two minutes." >&2
exit 1
