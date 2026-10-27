#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${JELLYPI_STACK_DIR:-}" ]]; then
  STACK_DIR="$JELLYPI_STACK_DIR"
elif [[ -f /opt/media-stack/compose.yaml ]]; then
  STACK_DIR=/opt/media-stack
else
  STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
cd "$STACK_DIR"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

docker compose config >/dev/null && pass "Compose configuration" || fail "Compose configuration"

mapfile -t containers < <(docker compose ps -aq)
if ((${#containers[@]} == 0)); then
  fail "No Compose containers exist"
else
  for id in "${containers[@]}"; do
    name="$(docker inspect -f '{{.Name}}' "$id" | sed 's#^/##')"
    state="$(docker inspect -f '{{.State.Status}}' "$id")"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id")"
    if [[ "$state" == running && "$health" != unhealthy ]]; then
      pass "$name state=$state health=$health"
    else
      fail "$name state=$state health=$health"
    fi
  done
fi

if docker inspect gluetun >/dev/null 2>&1 && docker inspect qbittorrent >/dev/null 2>&1; then
  gluetun_id="$(docker inspect -f '{{.Id}}' gluetun)"
  qbit_network="$(docker inspect -f '{{.HostConfig.NetworkMode}}' qbittorrent)"
  [[ "$qbit_network" == "container:$gluetun_id" ]] \
    && pass "qBittorrent shares Gluetun network namespace" \
    || fail "qBittorrent is not bound to Gluetun"
  docker exec qbittorrent sh -c \
    "grep -Eq '^Session\\\\(Interface|InterfaceName)=tun0$' /config/qBittorrent/qBittorrent.conf" \
    && pass "qBittorrent is bound to tun0" \
    || fail "qBittorrent is not bound to tun0"
fi

declare -A endpoints=(
  [Foreseerr]="http://127.0.0.1:5055/api/v1/status"
  [Radarr]="http://127.0.0.1:7878/ping"
  [Sonarr]="http://127.0.0.1:8989/ping"
  [Prowlarr]="http://127.0.0.1:9696/ping"
  [qBittorrent]="http://127.0.0.1:8080"
  [SABnzbd]="http://127.0.0.1:8081"
  [Bazarr]="http://127.0.0.1:6767"
  [Chaptarr]="http://127.0.0.1:8789"
  [Audiobookshelf]="http://127.0.0.1:13378/healthcheck"
  [AudioBookRequest]="http://127.0.0.1:8000"
  [Jellyfin]="http://127.0.0.1:8096/System/Info/Public"
  [Encoder_dashboard]="http://127.0.0.1:8097/api/overview"
)
declare -A service_names=(
  [Foreseerr]=seerr [Radarr]=radarr [Sonarr]=sonarr [Prowlarr]=prowlarr
  [qBittorrent]=qbittorrent [SABnzbd]=sabnzbd [Bazarr]=bazarr
  [Chaptarr]=chaptarr [Audiobookshelf]=audiobookshelf
  [AudioBookRequest]=audiobookrequest [Jellyfin]=jellyfin
  [Encoder_dashboard]=encoder-dashboard
)
running_services="$(docker compose ps --services --status running)"
for name in "${!endpoints[@]}"; do
  url="${endpoints[$name]}"
  service="${service_names[$name]}"
  if ! grep -qx "$service" <<<"$running_services"; then
    printf 'SKIP  %s is not enabled on this host\n' "$name"
    continue
  fi
  code="$(curl -sS -L --max-time 8 -o /dev/null -w '%{http_code}' "$url" || true)"
  if [[ "$code" =~ ^[23] ]] || [[ "$name:$code" == "qBittorrent:403" ]]; then
    pass "$name HTTP endpoint"
  else
    fail "$name HTTP endpoint ($url)"
  fi
done

media_root="${MEDIA_ROOT:-/srv/media}"
for path in Movies TV; do
  [[ -d "$media_root/$path" ]] && pass "$media_root/$path exists" || fail "$media_root/$path is missing"
done

if ((failures)); then
  printf '\n%d check(s) failed.\n' "$failures" >&2
  exit 1
fi
printf '\nAll enabled stack checks passed.\n'
