#!/usr/bin/env bash
set -u

COMPOSE_FILE="${JELLYPI_COMPOSE_FILE:-/opt/jellypi/compose.yaml}"
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
/usr/bin/docker compose -f "$COMPOSE_FILE" up -d --force-recreate gluetun qbittorrent

for _ in $(seq 1 24); do
  if vpn_path_ok; then
    echo "VPN and qBittorrent network path recovered."
    exit 0
  fi
  sleep 5
done

echo "The VPN path did not recover within two minutes." >&2
exit 1
