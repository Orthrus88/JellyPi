#!/usr/bin/env bash
set -euo pipefail

STACK_DIR="${JELLYPI_STACK_DIR:-/opt/media-stack}"
LOCK_FILE=/run/lock/jellypi-media-stack-update.lock

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Media stack update already running; exiting."; exit 0; }

cd "$STACK_DIR"
docker compose config >/dev/null
docker compose pull
docker compose up -d --remove-orphans

unhealthy=0
while IFS= read -r name; do
  state="$(docker inspect -f '{{.State.Status}}' "$name")"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name")"
  if [[ "$state" != running || "$health" == unhealthy ]]; then
    echo "$name failed its post-update check: state=$state health=$health" >&2
    unhealthy=1
  fi
done < <(docker compose ps -aq | xargs -r docker inspect -f '{{.Name}}' | sed 's#^/##')

exit "$unhealthy"
