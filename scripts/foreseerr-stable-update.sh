#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE=${COMPOSE_FILE:-/opt/media-stack/compose.yaml}
COMPOSE_DIR=${COMPOSE_DIR:-/opt/media-stack}
CONFIG_PARENT=${CONFIG_PARENT:-/var/lib/seerr}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/foreseerr}
IMAGE_REPO=ghcr.io/selmant/foreseerr
SERVICE=seerr
LOCK_FILE=/run/lock/foreseerr-stable-update.lock

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Foreseerr update already running; exiting."; exit 0; }

log() { printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"; }
compose() { docker compose -f "$COMPOSE_FILE" --project-directory "$COMPOSE_DIR" "$@"; }

read_current_tag() {
  python3 - "$COMPOSE_FILE" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
match = re.search(r'^\s*image:\s*ghcr\.io/selmant/foreseerr:(v\d+\.\d+\.\d+)\s*$', text, re.MULTILINE)
if not match:
    raise SystemExit('Could not find the stock Foreseerr image tag in compose.yaml')
print(match.group(1))
PY
}

read_latest_tag() {
  python3 <<'PY'
import json, re, urllib.request
request = urllib.request.Request(
    'https://api.github.com/repos/selmant/foreseerr/releases/latest',
    headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'JellyPi-Foreseerr-Updater/1.0'},
)
with urllib.request.urlopen(request, timeout=30) as response:
    release = json.load(response)
tag = release.get('tag_name', '')
if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
    raise SystemExit(f'Latest GitHub release is not a stable tag: {tag!r}')
print(tag)
PY
}

set_compose_tag() {
  NEW_TAG=$1 python3 - "$COMPOSE_FILE" <<'PY'
import os, pathlib, re, sys, tempfile
path = pathlib.Path(sys.argv[1])
text = path.read_text()
pattern = r'(^\s*image:\s*ghcr\.io/selmant/foreseerr:)(v\d+\.\d+\.\d+)(\s*$)'
updated, count = re.subn(pattern, lambda m: m.group(1) + os.environ['NEW_TAG'] + m.group(3), text, count=1, flags=re.MULTILINE)
if count != 1:
    raise SystemExit('Could not update the stock Foreseerr image tag')
fd, temporary = tempfile.mkstemp(prefix='.compose.', dir=str(path.parent))
try:
    with os.fdopen(fd, 'w') as output:
        output.write(updated)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

wait_ready() {
  local expected=$1 deadline=$((SECONDS + 240)) state reported
  while ((SECONDS < deadline)); do
    state=$(docker inspect "$SERVICE" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)
    if [[ "$state" == healthy ]]; then
      reported=$(curl -fsS --max-time 10 http://127.0.0.1:5055/api/v1/status 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))' 2>/dev/null || true)
      [[ "v$reported" == "$expected" ]] && return 0
    fi
    sleep 5
  done
  return 1
}

mkdir -p "$BACKUP_ROOT"
current_tag=$(read_current_tag)
latest_tag=$(read_latest_tag)
log "Current=$current_tag LatestStable=$latest_tag"
[[ "$current_tag" == "$latest_tag" ]] && { log 'Foreseerr is current.'; exit 0; }

log "Pulling stock Foreseerr $latest_tag before downtime."
docker pull "$IMAGE_REPO:$latest_tag"

stamp=$(date +%Y%m%d-%H%M%S)
backup_dir="$BACKUP_ROOT/$stamp-$current_tag-to-$latest_tag"
mkdir -p "$backup_dir"
cp -a "$COMPOSE_FILE" "$backup_dir/compose.yaml"
compose stop "$SERVICE"
if ! tar -C "$CONFIG_PARENT" -czf "$backup_dir/config.tar.gz" config; then
  compose up -d --no-deps "$SERVICE"
  exit 1
fi

set_compose_tag "$latest_tag"
if compose up -d --no-deps --force-recreate "$SERVICE" && wait_ready "$latest_tag"; then
  log "Foreseerr $latest_tag verified."
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf -- {} +
  exit 0
fi

log "Update failed; restoring $current_tag and its configuration."
compose stop "$SERVICE" || true
mv "$CONFIG_PARENT/config" "$backup_dir/failed-config" || true
tar -C "$CONFIG_PARENT" -xzf "$backup_dir/config.tar.gz"
cp -a "$backup_dir/compose.yaml" "$COMPOSE_FILE"
compose up -d --no-deps --force-recreate "$SERVICE"
wait_ready "$current_tag" || log 'Rollback requires manual attention.'
exit 1
